"""
ZorkAgent module for generating actions and managing game memory.
"""

import os
import re
from collections import Counter

from config import get_client_api_key, get_config
from hybrid_zork_extractor import ExtractorResponse
from llm_client import LLMClientWrapper
from map_graph import MapGraph


class ZorkAgent:
    """
    Handles agent action generation and memory management for Zork gameplay.
    """

    def __init__(
        self,
        agent_prompt: str = "agent.md",
        model: str = None,
        client: LLMClientWrapper | None = None,
        max_tokens: int | None = None,
        temperature: float = None,
        top_p: float = None,
        top_k: int = None,
        min_p: float = None,
        logger=None,
        episode_id: str = "unknown",
    ):
        """
        Initialize the ZorkAgent.

        Args:
            model: Model name for agent
            client: OpenAI client instance (if None, creates new one)
            max_tokens: Maximum tokens for agent responses
            temperature: Temperature for agent model
            top_p: Top-p nucleus sampling
            top_k: Top-k sampling
            min_p: Minimum probability sampling
            logger: Logger instance for tracking
            episode_id: Current episode ID for logging
        """

        self.agent_prompt = agent_prompt
        config = get_config()

        self.model = model or config.llm.agent_model
        self.max_tokens = max_tokens or config.agent_sampling.max_tokens
        self.temperature = (
            temperature
            if temperature is not None
            else config.agent_sampling.temperature
        )
        self.top_p = top_p if top_p is not None else config.agent_sampling.top_p
        self.top_k = top_k if top_k is not None else config.agent_sampling.top_k
        self.min_p = min_p if min_p is not None else config.agent_sampling.min_p
        self.logger = logger
        self.episode_id = episode_id

        # Prompt logging counter for temporary evaluation
        self.prompt_counter = 0
        self.enable_prompt_logging = config.logging.enable_prompt_logging

        # Initialize LLM client if not provided
        if client is None:
            self.client = LLMClientWrapper(
                base_url=config.llm.get_base_url_for_model("agent"),
                api_key=get_client_api_key(),
            )
        else:
            self.client = client

        # Load system prompt
        self._load_system_prompt()

    def _log_prompt_to_file(self, messages: list[dict], prefix: str = "agent") -> None:
        """Log the full prompt to a temporary file for evaluation."""
        if not self.enable_prompt_logging:
            return

        self.prompt_counter += 1
        filename = f"tmp/{prefix}_{self.prompt_counter:03d}.txt"

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"=== {prefix.upper()} PROMPT #{self.prompt_counter} ===\n")
                f.write(f"Model: {self.model}\n")
                f.write(f"Temperature: {self.temperature}\n")
                f.write(f"Top-p: {self.top_p}\n")
                f.write(f"Top-k: {self.top_k}\n")
                f.write(f"Min-p: {self.min_p}\n")
                f.write(f"Max Tokens: {self.max_tokens}\n")
                f.write(f"Episode ID: {self.episode_id}\n")
                f.write("=" * 50 + "\n\n")

                for i, message in enumerate(messages):
                    f.write(f"--- MESSAGE {i+1} ({message['role'].upper()}) ---\n")
                    f.write(message["content"])
                    f.write("\n\n")
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Failed to log prompt to {filename}: {e}",
                    extra={"episode_id": self.episode_id},
                )

    def _load_system_prompt(self) -> None:
        """Load agent system prompt from markdown files and enhance with knowledge."""
        try:
            # Load base agent prompt
            with open(self.agent_prompt) as fh:
                base_agent_prompt = fh.read()

            # Try to enhance with knowledge base
            self.system_prompt = self._enhance_prompt_with_knowledge(base_agent_prompt)

        except FileNotFoundError as e:
            if self.logger:
                self.logger.error(
                    f"Failed to load agent prompt file: {e}",
                    extra={"episode_id": self.episode_id},
                )
            raise

    def _enhance_prompt_with_knowledge(self, base_prompt: str) -> str:
        """Enhance the agent prompt with accumulated knowledge."""
        knowledge_file = "knowledgebase.md"

        if not os.path.exists(knowledge_file):
            return base_prompt

        try:
            with open(knowledge_file, encoding="utf-8") as f:
                knowledge_content = f.read()

            # Insert strategic guide before the "Output Format" section
            knowledge_section = f"""

**STRATEGIC GUIDE FROM PREVIOUS EPISODES:**

The following strategic guide has been compiled from analyzing previous episodes. Use this guide to improve your performance, prioritize important items, navigate efficiently, and avoid known dangers:

{knowledge_content}

**END OF STRATEGIC GUIDE**

"""

            if "**Output Format" in base_prompt:
                insertion_point = base_prompt.find("**Output Format")
                enhanced_prompt = (
                    base_prompt[:insertion_point]
                    + knowledge_section
                    + base_prompt[insertion_point:]
                )
            else:
                enhanced_prompt = base_prompt + knowledge_section

            # Log knowledge integration
            if self.logger:
                self.logger.info(
                    f"Enhanced prompt with knowledge base ({len(knowledge_content):,} characters)"
                )

            return enhanced_prompt

        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Could not load knowledge from {knowledge_file}: {e}"
                )
            return base_prompt

    def get_action(
        self,
        game_state_text: str,
        previous_actions_and_responses: list[dict] | None = None,
        action_counts: Counter | None = None,
        relevant_memories: str | None = None,
    ) -> str:
        """
        Gets an action from the Agent LM.

        Args:
            game_state_text: Current game state text
            previous_actions_and_responses: List of history dicts with action, response, score, reasoning, expected_outcome
            action_counts: Counter of how many times each action has been tried
            relevant_memories: Formatted string of relevant memories

        Returns:
            The agent's chosen action as a string
        """
        if "o1" in self.model:
            # Use user prompt for o1 models
            messages = [{"role": "user", "content": self.system_prompt}]
        else:
            messages = [{"role": "system", "content": self.system_prompt}]

        # Add history if provided
        if previous_actions_and_responses:
            memory_context = "Here's what you've done so far:\n"

            # Add the most recent actions and responses
            prev_score = previous_actions_and_responses[-9]["score"] if len(previous_actions_and_responses) >= 9 else 0
            for i, entry in enumerate(previous_actions_and_responses[-8:]):
                action = entry["action"]
                response = entry["response"]
                score = entry["score"]
                reasoning = entry.get("reasoning", "")
                expected_outcome = entry.get("expected_outcome", "")
                
                score_change = score - prev_score
                score_info = f" (Score: {score}" + (f", +{score_change})" if score_change > 0 else ")")
                memory_context += f"Command: {action}{score_info}\n"
                
                # Include previous reasoning and expected outcome
                if reasoning:
                    memory_context += f"Your reasoning: {reasoning}\n"
                if expected_outcome:
                    memory_context += f"You expected: {expected_outcome}\n"
                
                memory_context += f"Result: {response.strip()}\n\n"
                prev_score = score

            # Include information about repetitive actions
            # if action_counts:
            #     repeated_actions = [
            #         act for act, count in action_counts.items() if count > 2
            #     ]
            #     if repeated_actions:
            #         memory_context += "\n**CRITICAL WARNING**: You've tried these actions multiple times with limited success: "
            #         memory_context += ", ".join(repeated_actions)
            #         memory_context += ". According to your instructions, you must AVOID repeating failed actions and try completely different approaches.\n"

            if "o1" in self.model:
                # o1 models use user role for all messages
                messages.append({"role": "user", "content": memory_context})
            else:
                messages.append({"role": "system", "content": memory_context})

        # Combine game state with relevant memories if available
        user_content = game_state_text
        if relevant_memories:
            user_content = f"{user_content}\n\n{relevant_memories}"

        messages.append({"role": "user", "content": user_content})

        raise ValueError(messages)

        try:
            llm_response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **self.sampling_params.model_dump(exclude_unset=True),
            )
            action_response = llm_response.choices[0].message.content

            # Log the response for debugging
            self.logger.info(
                f"Agent LLM response: {action_response}",
                extra={
                    "event_type": "agent_llm_response",
                    "episode_id": self.episode_id,
                    "llm_response": action_response,
                    "model": self.model,
                },
            )

            # Simple parsing: extract action and reasoning from response
            action, reasoning = self._parse_action_response(action_response)

            # Store the parsed response for evaluation
            parsed_response = {"action": action, "reasoning": reasoning}

            # Log the final parsed action
            self.logger.info(
                f"Agent action parsed: {action}",
                extra={
                    "event_type": "agent_action_parsed",
                    "episode_id": self.episode_id,
                    "action": action,
                    "reasoning": reasoning,
                },
            )

            # Store the full chain for token analysis
            self.last_response_data = {
                "messages": messages,
                "response": action_response,
                "parsed": parsed_response,
            }

            return parsed_response

        except Exception as e:
            self.logger.error(
                f"Error getting agent action: {e}",
                extra={
                    "event_type": "agent_error",
                    "episode_id": self.episode_id,
                    "error": str(e),
                },
            )
            # Return a fallback action - let the critic evaluate it
            return {"action": "look", "reasoning": f"Error in action generation: {e}"}

    def get_action_with_reasoning(
        self,
        game_state_text: str,
        previous_actions_and_responses: list[dict] | None = None,
        action_counts: Counter | None = None,
        relevant_memories: str | None = None,
        user_input: str | None = None,
    ) -> dict[str, str]:
        """
        Gets an action from the Agent LM with reasoning preserved.

        Args:
            game_state_text: Current game state text
            previous_actions_and_responses: List of history dicts with action, response, score, reasoning, expected_outcome
            action_counts: Counter of how many times each action has been tried
            relevant_memories: Formatted string of relevant memories

        Returns:
            Dict with 'action' (cleaned), 'reasoning' (raw thinking/reasoning), and 'expected_outcome'
        """
        if "o1" in self.model:
            # Use user prompt for o1 models
            messages = [{"role": "user", "content": self.system_prompt}]
        else:
            messages = [{"role": "system", "content": self.system_prompt}]

        # Add history if provided
        if previous_actions_and_responses:
            memory_context = "Here's what you've done so far:\n"

            # Add the most recent actions and responses with score tracking
            prev_score = 0
            for i, entry in enumerate(previous_actions_and_responses):
                action = entry["action"]
                response = entry["response"]
                score = entry["score"]
                reasoning = entry.get("reasoning", "")
                expected_outcome = entry.get("expected_outcome", "")
                
                score_change = score - prev_score if i > 0 else 0
                score_info = f" (Score: {score}" + (f", +{score_change})" if score_change > 0 else ")")
                memory_context += f"Command: {action}{score_info}\n"
                
                # Include previous reasoning and expected outcome
                if reasoning:
                    memory_context += f"Your reasoning: {reasoning}\n"
                if expected_outcome:
                    memory_context += f"You expected: {expected_outcome}\n"
                
                memory_context += f"Result: {response.strip()}\n\n"
                prev_score = score

            # Include information about repetitive actions
            # if action_counts:
            #     repeated_actions = [
            #         act for act, count in action_counts.items() if count > 2
            #     ]
            #     if repeated_actions:
            #         memory_context += "\n**CRITICAL WARNING**: You've tried these actions multiple times with limited success: "
            #         memory_context += ", ".join(repeated_actions)
            #         memory_context += ". According to your instructions, you must AVOID repeating failed actions and try completely different approaches.\n"

            if "o1" in self.model:
                # o1 models use user role for all messages
                messages.append({"role": "user", "content": memory_context})
            else:
                messages.append({"role": "system", "content": memory_context})

        # Combine game state with relevant memories if available
        user_content = game_state_text
        if relevant_memories:
            user_content += f"\n\n{relevant_memories}"

        if user_input:
            user_content += f"\n\nYour hear a voice in your head saying: {user_input}"

        messages.append({"role": "user", "content": user_content})

        # Log the full prompt for evaluation
        self._log_prompt_to_file(messages, "agent")

        try:
            client_args = dict(
                model=self.model,
                messages=messages,
                stop=None,
                temperature=self.temperature,
                top_p=self.top_p,
                top_k=self.top_k,
                min_p=self.min_p,
                max_tokens=self.max_tokens,
            )

            response = self.client.chat.completions.create(**client_args)
            raw_response = response.content.strip()

            # DEBUG: Log the raw response to understand what the model is actually returning
            # if self.logger:
            #     self.logger.info(
            #         "[DEBUG] Raw agent response:",
            #         extra={
            #             "event_type": "agent_raw_response_debug",
            #             "episode_id": self.episode_id,
            #             "model": self.model,
            #             "raw_response": raw_response,
            #             "raw_response_length": len(raw_response),
            #         },
            #     )

            # Extract reasoning from thinking tags
            reasoning_parts = []

            # Extract <think> tags
            think_matches = re.findall(
                r"<think>(.*?)</think>", raw_response, flags=re.DOTALL
            )
            reasoning_parts.extend(think_matches)

            # Extract <thinking> tags
            thinking_matches = re.findall(
                r"<thinking>(.*?)</thinking>", raw_response, flags=re.DOTALL
            )
            reasoning_parts.extend(thinking_matches)

            # Extract <reflection> tags
            reflection_matches = re.findall(
                r"<reflection>(.*?)</reflection>", raw_response, flags=re.DOTALL
            )
            reasoning_parts.extend(reflection_matches)

            # Extract <expected_outcome> tags
            expected_outcome_matches = re.findall(
                r"<expected_outcome>(.*?)</expected_outcome>", raw_response, flags=re.DOTALL
            )
            expected_outcome = "\n\n".join(
                match.strip() for match in expected_outcome_matches if match.strip()
            ) if expected_outcome_matches else None

            # Extract <memory> tags for manual memory creation
            memory_matches = re.findall(
                r"<memory>(.*?)</memory>", raw_response, flags=re.DOTALL
            )
            new_memories = []
            for mem_text in memory_matches:
                # Parse the memory text
                mem_dict = self._parse_memory_block(mem_text)
                if mem_dict:
                    new_memories.append(mem_dict)

            # DEBUG: Log what reasoning parts were extracted
            # if self.logger:
            #     self.logger.info(
            #         "[DEBUG] Reasoning extraction results:",
            #         extra={
            #             "event_type": "reasoning_extraction_debug",
            #             "episode_id": self.episode_id,
            #             "think_matches_count": len(think_matches),
            #             "thinking_matches_count": len(thinking_matches),
            #             "reflection_matches_count": len(reflection_matches),
            #             "expected_outcome_matches_count": len(expected_outcome_matches),
            #             "total_reasoning_parts": len(reasoning_parts),
            #             "think_matches": think_matches,
            #             "thinking_matches": thinking_matches,
            #             "reflection_matches": reflection_matches,
            #             "expected_outcome_matches": expected_outcome_matches,
            #         },
            #     )

            # Fallback: if no reasoning found in tags, try to extract reasoning from the response
            if not reasoning_parts:
                # Look for reasoning patterns that might not be in tags
                lines = raw_response.split("\n")
                potential_reasoning = []

                for line in lines:
                    line = line.strip()
                    # Skip if it looks like a command
                    if len(line.split()) <= 3 and any(
                        word.lower() in line.lower()
                        for word in [
                            "north",
                            "south",
                            "east",
                            "west",
                            "up",
                            "down",
                            "look",
                            "examine",
                            "take",
                            "open",
                            "close",
                            "enter",
                            "exit",
                            "climb",
                            "go",
                        ]
                    ):
                        continue
                    # Skip empty lines
                    if not line:
                        continue
                    # If it's a longer explanatory line, consider it reasoning
                    if len(line) > 20 or any(
                        reasoning_word in line.lower()
                        for reasoning_word in [
                            "should",
                            "need",
                            "want",
                            "will",
                            "can",
                            "might",
                            "could",
                            "seems",
                            "appears",
                            "because",
                            "since",
                            "to explore",
                            "to find",
                        ]
                    ):
                        potential_reasoning.append(line)

                if potential_reasoning:
                    reasoning_parts.extend(potential_reasoning)

                # DEBUG: Log fallback reasoning extraction
                if self.logger:
                    self.logger.info(
                        "[DEBUG] Fallback reasoning extraction:",
                        extra={
                            "event_type": "fallback_reasoning_debug",
                            "episode_id": self.episode_id,
                            "potential_reasoning_lines": potential_reasoning,
                            "fallback_reasoning_count": len(potential_reasoning),
                        },
                    )

            # Combine all reasoning
            reasoning = "\n\n".join(
                part.strip() for part in reasoning_parts if part.strip()
            )

            # DEBUG: Log final reasoning result
            # if self.logger:
            #     self.logger.info(
            #         "[DEBUG] Final reasoning result:",
            #         extra={
            #             "event_type": "final_reasoning_debug",
            #             "episode_id": self.episode_id,
            #             "final_reasoning": reasoning,
            #             "final_reasoning_length": len(reasoning),
            #             "reasoning_is_none": reasoning is None or reasoning == "",
            #         },
            #     )

            # Clean up the action: remove any thinking, expected_outcome, and memory tags
            action = re.sub(r"<think>.*?</think>\s*", "", raw_response, flags=re.DOTALL)
            action = re.sub(r"<thinking>.*?</thinking>\s*", "", action, flags=re.DOTALL)
            action = re.sub(
                r"<reflection>.*?</reflection>\s*", "", action, flags=re.DOTALL
            )
            action = re.sub(
                r"<expected_outcome>.*?</expected_outcome>\s*", "", action, flags=re.DOTALL
            )
            action = re.sub(
                r"<memory>.*?</memory>\s*", "", action, flags=re.DOTALL
            )

            # Remove any remaining markup tags (like <s>, </s>, etc.)
            action = re.sub(r"<[^>]*>", "", action)

            # Remove backticks and other formatting
            action = re.sub(
                r"`([^`]*)`", r"\1", action
            )  # Remove backticks but keep content
            action = re.sub(
                r"```[^`]*```", "", action, flags=re.DOTALL
            )  # Remove code blocks

            # Basic cleaning: Zork commands are usually lowercase
            action = action.lower().strip()

            # Remove any leading/trailing punctuation that might interfere
            action = action.strip(".,!?;:")

            # Validate action is not empty
            if not action or action.isspace():
                if self.logger:
                    self.logger.warning(
                        "Agent returned empty action, using 'look' as fallback"
                    )
                action = "look"

            return {
                "action": action,
                "reasoning": reasoning if reasoning else None,
                "expected_outcome": expected_outcome,
                "new_memories": new_memories if new_memories else [],
                "raw_response": raw_response,
            }
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error getting agent action: {e}",
                    extra={"episode_id": self.episode_id},
                )
            return {
                "action": "look",
                "reasoning": None,
                "expected_outcome": None,
                "new_memories": [],
                "raw_response": None,
            }  # Default safe action on error

    def _parse_memory_block(self, memory_text: str) -> dict | None:
        """
        Parse a <memory> block from agent response.
        
        Expected format:
            subject_type: room|object|character
            subject_id: Kitchen
            content: Description here
            tags: tag1,tag2
            confidence: 0.8
        
        Returns:
            Dict with parsed memory or None if invalid
        """
        try:
            lines = [line.strip() for line in memory_text.strip().split('\n') if line.strip()]
            mem_dict = {}
            
            for line in lines:
                # Skip comment lines
                if line.startswith('<!--') or line.startswith('#'):
                    continue
                
                # Parse key: value format
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().lower()
                    value = value.strip()
                    
                    if key == 'subject_type':
                        if value in ['room', 'object', 'character']:
                            mem_dict['subject_type'] = value
                    elif key == 'subject_id':
                        mem_dict['subject_id'] = value
                    elif key == 'content':
                        mem_dict['content'] = value
                    elif key == 'tags':
                        # Split tags by comma
                        mem_dict['tags'] = [t.strip() for t in value.split(',') if t.strip()]
                    elif key == 'confidence':
                        try:
                            conf = float(value)
                            if 0.0 <= conf <= 1.0:
                                mem_dict['confidence'] = conf
                        except ValueError:
                            pass
            
            # Validate required fields
            if 'subject_type' in mem_dict and 'subject_id' in mem_dict and 'content' in mem_dict:
                # Set defaults for optional fields
                if 'tags' not in mem_dict:
                    mem_dict['tags'] = []
                if 'confidence' not in mem_dict:
                    mem_dict['confidence'] = 0.7
                
                return mem_dict
            
            return None
            
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to parse memory block: {e}")
            return None

    def get_relevant_memories_for_prompt(
        self,
        current_location_name_from_current_extraction: str,
        memory_log_history: list[ExtractorResponse],
        current_inventory: list[str],
        game_map: MapGraph,
        previous_room_name_for_map_context: str | None = None,
        action_taken_to_current_room: str | None = None,
        in_combat: bool = False,
        failed_actions_by_location: dict | None = None,
    ) -> str:
        """
        Generate relevant memories and context for the agent prompt.

        Args:
            current_location_name_from_current_extraction: Current room name
            memory_log_history: History of extracted information
            current_inventory: Current inventory items
            game_map: The game map object
            previous_room_name_for_map_context: Previous room name
            action_taken_to_current_room: Action that led to current room
            in_combat: Whether currently in combat
            failed_actions_by_location: Dict of failed actions by location

        Returns:
            Formatted string of relevant memories for the agent
        """
        # Check for loop situation - if agent has been in same location for multiple recent turns
        recent_locations = []
        if memory_log_history and len(memory_log_history) >= 3:
            # Check last 5 turns for same location
            for obs in memory_log_history[-5:]:
                if obs.current_location_name:
                    recent_locations.append(obs.current_location_name)

        # Count how many of the recent turns were in current location
        current_location_count = recent_locations.count(
            current_location_name_from_current_extraction
        )
        is_stuck_in_loop = current_location_count >= 3

        map_context_str = ""
        if game_map:
            map_info = game_map.get_context_for_prompt(
                current_room_name=current_location_name_from_current_extraction,
                previous_room_name=previous_room_name_for_map_context,
                action_taken_to_current=action_taken_to_current_room,
            )

            # Add navigation suggestions to the map context
            nav_suggestions = game_map.get_navigation_suggestions(
                current_location_name_from_current_extraction
            )
            if nav_suggestions:
                nav_text = "Available exits: " + ", ".join(
                    [
                        f"{suggestion['exit']} (to {suggestion['destination']})"
                        for suggestion in nav_suggestions
                    ]
                )

                # If stuck in loop, make navigation more prominent
                if is_stuck_in_loop:
                    nav_text = f"🚨 LOOP DETECTED - PRIORITIZE MOVEMENT! 🚨\n{nav_text}\n⚠️  You've been in {current_location_name_from_current_extraction} for {current_location_count} recent turns. Try these exits NOW!"

                if map_info:
                    map_info += f"\n{nav_text}"
                else:
                    map_info = f"--- Map Information ---\n{nav_text}"

            if map_info:
                map_context_str = map_info

        other_memory_strings = []

        # Add loop detection warning at the top of other memories
        if is_stuck_in_loop:
            other_memory_strings.append(
                f"🔄 CRITICAL LOOP WARNING: You have been in '{current_location_name_from_current_extraction}' for {current_location_count} of your last 5 turns! STOP object interactions and try MOVEMENT commands immediately. Check the Available exits above and use basic directional commands like 'north', 'south', 'east', 'west'."
            )

        # Add combat status information
        if in_combat:
            other_memory_strings.append(
                "- COMBAT SITUATION: You are currently in combat or facing an immediate threat! Be prepared to defend yourself or flee."
            )

        if current_inventory:
            other_memory_strings.append(
                f"- You are carrying: {', '.join(current_inventory)}."
            )

        # Add failed actions warning for current location
        if (
            failed_actions_by_location
            and current_location_name_from_current_extraction
            in failed_actions_by_location
        ):
            failed_actions = failed_actions_by_location[
                current_location_name_from_current_extraction
            ]
            if failed_actions:
                other_memory_strings.append(
                    f"- FAILED ACTIONS in {current_location_name_from_current_extraction}: The following actions have already failed here and should NOT be repeated: {', '.join(sorted(failed_actions))}."
                )

        previous_observations_of_current_room = [
            obs
            for obs in reversed(memory_log_history[:-1])  # Exclude current observation
            if obs.current_location_name
            == current_location_name_from_current_extraction
        ]

        if previous_observations_of_current_room:
            last_relevant_obs = previous_observations_of_current_room[0]
            prev_objects = last_relevant_obs.visible_objects
            if prev_objects:
                other_memory_strings.append(
                    f"- Previously noted objects in {current_location_name_from_current_extraction}: {', '.join(prev_objects)}."
                )

        if memory_log_history:
            # Always use the most recent memory entry
            # This contains the result from the last action taken
            relevant_history_index = -1
            last_turn_info = memory_log_history[relevant_history_index]
            important_msgs = last_turn_info.important_messages
            action_results = [
                msg
                for msg in important_msgs
                if not msg.lower().startswith("you are")
                and not msg.lower().startswith(
                    current_location_name_from_current_extraction.lower()
                )
                and len(msg) < 100
            ]
            if action_results:
                other_memory_strings.append(
                    f"- Last action result/event: {' '.join(action_results)}."
                )

        final_output_parts = []
        if map_context_str and map_context_str.strip():
            content_part = map_context_str.replace(
                "--- Map Information ---", ""
            ).strip()
            if content_part:  # Only add if there's more than just the header
                final_output_parts.append(map_context_str)

        if other_memory_strings:  # other_memory_strings is populated by existing logic
            if final_output_parts:
                final_output_parts.append("\n--- Other Relevant Memories ---")
            else:
                final_output_parts.append("--- Relevant Memories ---")
            final_output_parts.extend(other_memory_strings)

        if not final_output_parts:
            return ""
        return "\n".join(final_output_parts) + "\n"

    def update_episode_id(self, episode_id: str) -> None:
        """Update the episode ID for logging purposes."""
        self.episode_id = episode_id

    def reload_knowledge_base(self) -> bool:
        """Reload the knowledge base from file and update the system prompt.

        Returns:
            True if knowledge base was successfully reloaded, False otherwise
        """
        try:
            # Load base agent prompt
            with open("agent.md") as fh:
                base_agent_prompt = fh.read()

            # Re-enhance with current knowledge base
            new_system_prompt = self._enhance_prompt_with_knowledge(base_agent_prompt)

            # Update the system prompt
            old_length = (
                len(self.system_prompt) if hasattr(self, "system_prompt") else 0
            )
            self.system_prompt = new_system_prompt
            new_length = len(self.system_prompt)

            if self.logger:
                self.logger.info(
                    f"Knowledge base reloaded successfully (prompt: {old_length} -> {new_length} chars)",
                    extra={
                        "event_type": "knowledge_base_reloaded",
                        "episode_id": self.episode_id,
                        "old_prompt_length": old_length,
                        "new_prompt_length": new_length,
                    },
                )

            return True

        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Failed to reload knowledge base: {e}",
                    extra={"episode_id": self.episode_id},
                )
            return False
