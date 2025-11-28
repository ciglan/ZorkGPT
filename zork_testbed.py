"""Minimal Zork testbed.

This testbed runs a very simple agent against the Zork interface without any
knowledge base, critic, or information extractor. It demonstrates how to:

- Start the Zork process and obtain the initial state
- Send actions and read responses each turn
- Track and print score and termination conditions
- Save and load complete game states (Zork save, memory, history)

SAVE/LOAD FEATURES:
-------------------
The testbed includes a comprehensive save/load system that persists:
1. Zork game state (via Zork's native save/restore)
2. Memory state (ActionMemoryManager data including spatial memory)
3. History of actions and responses
4. Metadata (turn number, score, location, action counts, etc.)

Auto-save:
- Automatically saves on turns where turn % 10 == 1 (turns 1, 11, 21, 31, ...)
- Saves are stored in saves/autosave_turn_N/ directories

Manual save/load (when user_commands_enabled=True):
- cmd:savestate <name> - Save complete game state with a custom name
- cmd:loadstate <name> - Load a previously saved game state
- cmd:liststates - List all available saves with metadata

Save structure:
saves/<save_name>/
  ├── zork_save.dat          # Zork game state file
  ├── memory/                # Memory data directory
  │   ├── historical_memory.json
  │   └── episodes/...
  ├── history.json           # History list
  └── metadata.json          # Turn, score, location, etc.

TODO:
add memory
memory creation
extract triples prev_state, action, next_state
special case for movement - state is room; action is movement; next_state is room or failure
special case for object interaction - state is room, object; action is object interaction; sucess  / failure

ability to detect change of location
for each entity in room, maintains the count of instances it was present when we moved in
for each edge, maintain count


"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import random
import re
import shutil
import time
from typing import Optional

from config import get_client_api_key, get_config
from hybrid_zork_extractor import ExtractorResponse, HybridZorkExtractor
from llm_client import LLMClientWrapper
from logger import setup_logging
from memory import ActionMemoryManager
from tts_manager import TTSManager
from zork_agent import ZorkAgent
from zork_api import ZorkInterface


class SimpleAgent:
    """A minimal, stateless agent that issues basic Zork commands.

    The agent is intentionally simple: it alternates between looking around,
    trying to take items, checking inventory, and moving in random directions.
    This is meant as a starting point for experimentation.
    """

    def __init__(self, seed: int | None = None) -> None:
        self.random = random.Random(seed)

        self.movement_actions: list[str] = [
            "north",
            "south",
            "east",
            "west",
            "up",
            "down",
        ]

        self.utility_actions: list[str] = [
            "look",
            "take all",
            "inventory",
        ]

    def choose_action(
        self, game_state_text: str, history: list[tuple[str, str]], turn: int
    ) -> str:
        """Pick a basic command to send to Zork.

        Args:
            game_state_text: The latest text returned by the game.
            history: List of (action, response) tuples from previous turns.
            turn: Current turn number (1-based).

        Returns:
            The action string to send to Zork.
        """
        if turn == 1:
            return "look"

        # Occasionally check inventory
        if turn % 7 == 0:
            return "inventory"

        # Try to take visible items occasionally
        if turn % 5 == 0:
            return "take all"

        # Otherwise, explore randomly
        if self.random.random() < 0.7:
            return self.random.choice(self.movement_actions)

        return self.random.choice(self.utility_actions)


class ZorkTestbed:
    def __init__(self, max_turns: int = 6000,
        user_commands_enabled: bool = False,
        turn_delay_seconds: float = 0.25,
        episode_id: str = "testbed_episode",
        enable_memory: bool = True,
        enable_analyst: bool = True,
        enable_tts: bool = False,
        agent_history_limit: int = 40,
        enable_manual_memory: bool = True,
        enable_extractor: bool = True) -> None:
        """
        Initialize the Zork testbed.

        Args:
            max_turns: Maximum number of turns before stopping
            user_commands_enabled: Enable interactive commands (cmd:savestate, cmd:loadstate, etc.)
            turn_delay_seconds: Delay between turns in seconds
            episode_id: Unique identifier for this episode (used for memory persistence)
            enable_memory: Enable ActionMemoryManager for learning from past episodes
            enable_analyst: Enable GameAnalyst for strategic analysis
            enable_tts: Enable text-to-speech output
            agent_history_limit: Number of recent history entries to pass to agent (default: 40)
            enable_manual_memory: Enable ManualMemoryStore for agent-initiated notes
            enable_extractor: Enable HybridZorkExtractor for information extraction
        """
        config = get_config()

        self.episode_log_file = config.files.episode_log_file
        self.json_log_file = config.files.json_log_file
        self.logger = setup_logging(self.episode_log_file, self.json_log_file)
        
        # Memory system flag
        self.enable_memory = enable_memory
        # Analyst system flag
        self.enable_analyst = enable_analyst
        # TTS system flag
        self.enable_tts = enable_tts
        # Manual memory system flag
        self.enable_manual_memory = enable_manual_memory
        # Extractor system flag
        self.enable_extractor = enable_extractor

        agent_client = LLMClientWrapper(
            base_url=config.llm.get_base_url_for_model("agent"),
            api_key=get_client_api_key(),
            logger=self.logger,
        )

        # Info extractor client
        extractor_client = LLMClientWrapper(
            base_url=config.llm.get_base_url_for_model("info_ext"),
            api_key=get_client_api_key(),
            logger=self.logger,
        )

        # Initialize core components with their specific clients
        self.agent = ZorkAgent(client=agent_client, logger=self.logger, agent_prompt="simple_agent.md")

        # Initialize hybrid extractor (combines structured parsing with LLM extraction)
        if self.enable_extractor:
            self.extractor = HybridZorkExtractor(
                client=extractor_client, logger=self.logger
            )
        else:
            self.extractor = None
            print("⚠️  Information extractor disabled - agent will not have structured game state analysis")

        # Initialize action and spatial memory (optional)
        if self.enable_memory:
            self.action_memory = ActionMemoryManager(
                episode_id=episode_id,
                data_dir="memory_store",
                enable_persistence=True,
                enable_spatial_memory=True,
                auto_save_interval=5  # Save every 5 turns
            )
        else:
            self.action_memory = None
            print("⚠️  Memory system disabled - agent will not learn from previous runs")

        # Initialize manual memory store for agent-initiated notes (optional)
        if self.enable_manual_memory:
            from memory.manual_memory_store import ManualMemoryStore
            self.manual_memory = ManualMemoryStore(
                episode_id=episode_id,
                data_dir="memory_store",
                use_global=False,
                logger=self.logger
            )
        else:
            self.manual_memory = None
            print("⚠️  Manual memory disabled - agent cannot store/retrieve explicit notes")
        
        
        # Initialize game analyst for strategic analysis (optional)
        if self.enable_analyst:
            from game_analyst import GameAnalyst
            analyst_client = LLMClientWrapper(
                base_url=config.llm.get_base_url_for_model("analysis"),
                api_key=get_client_api_key(),
                logger=self.logger,
            )
            self.analyst = GameAnalyst(
                client=analyst_client,
                model=config.llm.analysis_model,  # Pull from config
                analysis_interval=10,  # Default: analyze every 10 turns
                logger=self.logger
            )
        else:
            self.analyst = None

        # Initialize text-to-speech (optional)
        self.tts = TTSManager(
            enabled=self.enable_tts,
            backend='edge',
            voices={
                'agent': 'en-US-GuyNeural',  # Female AI
                'game': 'en-US-JennyNeural'       # Male narrator
            }
        )
        

        self.max_turns = max_turns
        self.turn_delay_seconds = turn_delay_seconds
        self.agent_history_limit = agent_history_limit  # How many history entries to pass to agent
        # self.agent = SimpleAgent()

        self.zork = ZorkInterface(timeout=1.0)
        # History now includes agent thinking: action, response, score, reasoning, expected_outcome
        self.history: list[dict] = []  # [{action, response, score, reasoning, expected_outcome}]
        self.current_score = 0
        self.max_score = 0
        self.previous_score = 0  # Track previous score for change detection
        self.current_location = None
        self.previous_location = None
        self.current_state = ""
        self.next_state = ""
        self.turn = 0
        self.game_over = False
        self.last_extraction = None  # Store last extraction for manual memory retrieval
        self.reason = ""
        self.user_input = ""
        self.turn_extracted = None
        self.turn_extracted_score = 0
        self.turn_extracted_max_score = 0
        self.action_counts = Counter()
        self.user_commands_enabled = user_commands_enabled
        self.current_inventory = []  # Track inventory for analyst
        
        # Save/load configuration
        self.saves_dir = Path("saves")
        self.saves_dir.mkdir(parents=True, exist_ok=True)

    def save_game_state(self, save_name: str) -> bool:
        """
        Save complete game state including Zork save, memory, and history.
        
        Args:
            save_name: Name for this save (will create a directory)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create save directory
            save_dir = self.saves_dir / save_name
            save_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"\n💾 Saving game state to '{save_name}'...")
            
            # 1. Save Zork game state
            zork_save_file = save_dir / "zork_save.dat"
            zork_result = self.zork.send_interactive_command(
                "save", 
                str(zork_save_file.absolute()),
                "Please enter a filename", 
                "Ok."
            )
            print(f"   ✓ Zork game state saved")
            
            # 2. Save memory state (if memory is enabled)
            if self.enable_memory and self.action_memory:
                memory_dir = save_dir / "memory"
                memory_dir.mkdir(parents=True, exist_ok=True)
                
                # Temporarily change the memory data_dir to save to this location
                original_data_dir = self.action_memory.data_dir
                self.action_memory.data_dir = memory_dir
                self.action_memory.save_to_disk()
                self.action_memory.data_dir = original_data_dir
                
                print(f"   ✓ Memory state saved ({len(self.action_memory.location_patterns)} locations)")
            
            # Save manual memory state (if enabled)
            if self.enable_manual_memory and self.manual_memory:
                memory_dir = save_dir / "memory"
                memory_dir.mkdir(parents=True, exist_ok=True)
                
                # Save manual memories to the save directory
                manual_mem_file = memory_dir / "manual_memories.json"
                original_file = self.manual_memory.episode_file
                self.manual_memory.episode_file = manual_mem_file
                self.manual_memory.save_to_disk()
                self.manual_memory.episode_file = original_file
                
                total_notes = sum(
                    len(notes) for notes in 
                    list(self.manual_memory.locations.values()) +
                    list(self.manual_memory.objects.values()) +
                    list(self.manual_memory.characters.values())
                )
                print(f"   ✓ Manual memory saved ({total_notes} notes)")
            
            # 3. Save history
            history_file = save_dir / "history.json"
            # History is already in dict format, just save it directly
            with open(history_file, "w") as f:
                json.dump(self.history, f, indent=2)
            print(f"   ✓ History saved ({len(self.history)} turns)")
            
            # 4. Save metadata (current state variables)
            metadata_file = save_dir / "metadata.json"
            metadata = {
                "turn": self.turn,
                "current_score": self.current_score,
                "max_score": self.max_score,
                "previous_score": self.previous_score,
                "current_location": self.current_location,
                "previous_location": self.previous_location,
                "episode_id": self.action_memory.episode_id if self.action_memory else None,
                "action_counts": dict(self.action_counts),
                "current_inventory": self.current_inventory,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)
            print(f"   ✓ Metadata saved")
            
            print(f"✅ Save complete: {save_name} (Turn {self.turn}, Score {self.current_score})\n")
            return True
            
        except Exception as e:
            print(f"❌ Error saving game state: {e}")
            self.logger.error(f"Failed to save game state '{save_name}': {e}")
            return False

    def load_game_state(self, save_name: str) -> bool:
        """
        Load complete game state including Zork save, memory, and history.
        
        Args:
            save_name: Name of the save to load
            
        Returns:
            True if successful, False otherwise
        """
        try:
            save_dir = self.saves_dir / save_name
            
            if not save_dir.exists():
                print(f"❌ Save '{save_name}' not found")
                return False
            
            print(f"\n📂 Loading game state from '{save_name}'...")
            
            # 1. Restore Zork game state
            zork_save_file = save_dir / "zork_save.dat"
            if not zork_save_file.exists():
                print(f"❌ Zork save file not found in '{save_name}'")
                return False
                
            zork_result = self.zork.send_interactive_command(
                "restore",
                str(zork_save_file.absolute()),
                "Please enter a filename",
                "Ok."
            )
            print(f"   ✓ Zork game state restored")
            
            # 2. Load memory state (if memory is enabled)
            if self.enable_memory and self.action_memory:
                memory_dir = save_dir / "memory"
                if memory_dir.exists():
                    # Copy memory files to the active memory directory
                    active_memory_dir = Path(self.action_memory.data_dir)
                    
                    # Backup current memory if it exists
                    if active_memory_dir.exists():
                        backup_dir = active_memory_dir.parent / f"{active_memory_dir.name}_backup_{int(time.time())}"
                        shutil.copytree(active_memory_dir, backup_dir)
                        print(f"   ℹ️  Current memory backed up to {backup_dir.name}")
                    
                    # Copy saved memory to active directory
                    if active_memory_dir.exists():
                        shutil.rmtree(active_memory_dir)
                    shutil.copytree(memory_dir, active_memory_dir)
                    
                    # Reload memory from the copied files
                    self.action_memory._load_persistent_memory()
                    
                    print(f"   ✓ Memory state restored ({len(self.action_memory.location_patterns)} locations)")
                else:
                    print(f"   ⚠️  No memory data found in save")
            
            # Load manual memory state (if enabled)
            if self.enable_manual_memory and self.manual_memory:
                manual_mem_file = save_dir / "memory" / "manual_memories.json"
                if manual_mem_file.exists():
                    # Backup current manual memories
                    if self.manual_memory.episode_file.exists():
                        backup_file = self.manual_memory.data_dir / f"manual_memories_backup_{int(time.time())}.json"
                        shutil.copy(self.manual_memory.episode_file, backup_file)
                    
                    # Copy saved manual memories to active location
                    shutil.copy(manual_mem_file, self.manual_memory.episode_file)
                    
                    # Reload manual memories
                    self.manual_memory._load_from_disk()
                    
                    total_notes = sum(
                        len(notes) for notes in 
                        list(self.manual_memory.locations.values()) +
                        list(self.manual_memory.objects.values()) +
                        list(self.manual_memory.characters.values())
                    )
                    print(f"   ✓ Manual memory restored ({total_notes} notes)")
                else:
                    print(f"   ⚠️  No manual memory data found in save")
            
            # 3. Load history
            history_file = save_dir / "history.json"
            if history_file.exists():
                with open(history_file, "r") as f:
                    history_data = json.load(f)
                
                # Convert history to new dict format, handling both old and new formats
                self.history = []
                for item in history_data:
                    if isinstance(item, dict):
                        # New format or already a dict - use as-is, ensure all fields exist
                        self.history.append({
                            "action": item.get("action", ""),
                            "response": item.get("response", ""),
                            "score": item.get("score", 0),
                            "reasoning": item.get("reasoning", ""),
                            "expected_outcome": item.get("expected_outcome", "")
                        })
                    else:
                        # Old format (tuple/list) - convert to dict without reasoning
                        self.history.append({
                            "action": item[0] if len(item) > 0 else "",
                            "response": item[1] if len(item) > 1 else "",
                            "score": item[2] if len(item) > 2 else 0,
                            "reasoning": "",
                            "expected_outcome": ""
                        })
                
                print(f"   ✓ History restored ({len(self.history)} turns)")
            else:
                print(f"   ⚠️  No history file found in save")
                self.history = []
            
            # 4. Load metadata
            metadata_file = save_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    metadata = json.load(f)
                
                self.turn = metadata.get("turn", 0)
                self.current_score = metadata.get("current_score", 0)
                self.max_score = metadata.get("max_score", 0)
                self.previous_score = metadata.get("previous_score", 0)
                self.current_location = metadata.get("current_location")
                self.previous_location = metadata.get("previous_location")
                self.current_inventory = metadata.get("current_inventory", [])
                
                # Restore action counts
                action_counts_dict = metadata.get("action_counts", {})
                self.action_counts = Counter(action_counts_dict)
                
                saved_at = metadata.get("saved_at", "unknown")
                print(f"   ✓ Metadata restored (saved at {saved_at})")
            else:
                print(f"   ⚠️  No metadata file found in save")
            
            print(f"✅ Load complete: {save_name}")
            print(f"   🎮 Resuming from Turn {self.turn}, Score {self.current_score}")
            print(f"   📍 Location: {self.current_location}")
            print(f"   📜 History: {len(self.history)} actions loaded\n")
            return True
            
        except Exception as e:
            print(f"❌ Error loading game state: {e}")
            self.logger.error(f"Failed to load game state '{save_name}': {e}")
            return False

    def auto_save(self) -> None:
        """Auto-save the game state with a timestamped name."""
        save_name = f"autosave_turn_{self.turn}"
        self.save_game_state(save_name)

    def _process_agent_memories(self, proposed_memories: list[dict], turn: int) -> None:
        """
        Validate and store agent-proposed memories.
        
        Args:
            proposed_memories: List of memory dicts from agent
            turn: Current turn number
        """
        if not proposed_memories:
            return
        
        # Enforce per-turn limit
        MAX_MEMORIES_PER_TURN = 3
        if len(proposed_memories) > MAX_MEMORIES_PER_TURN:
            self.logger.warning(
                f"Agent proposed {len(proposed_memories)} memories, limiting to {MAX_MEMORIES_PER_TURN}"
            )
            proposed_memories = proposed_memories[:MAX_MEMORIES_PER_TURN]
        
        accepted = 0
        rejected = 0
        
        for mem in proposed_memories:
            try:
                # Validate and add memory
                note = self.manual_memory.add_memory(
                    subject_type=mem['subject_type'],
                    subject_id=mem['subject_id'],
                    content=mem['content'],
                    tags=mem.get('tags', []),
                    confidence=mem.get('confidence', 0.7),
                    author='agent',
                    turn=turn
                )
                accepted += 1
                
                # Log successful memory creation
                print(f"📝 Agent created memory: {mem['subject_type']}:{mem['subject_id']} - {mem['content'][:50]}...")
                self.logger.info(
                    f"Agent memory created: {note.id}",
                    extra={
                        "subject_type": note.subject_type,
                        "subject_id": note.subject_id,
                        "content": note.content,
                        "tags": note.tags,
                        "turn": turn
                    }
                )
                
            except ValueError as e:
                # Validation failed
                rejected += 1
                print(f"⚠️  Agent memory rejected: {e}")
                self.logger.warning(
                    f"Agent memory rejected: {e}",
                    extra={
                        "proposed_memory": mem,
                        "turn": turn
                    }
                )
            except Exception as e:
                # Unexpected error
                rejected += 1
                print(f"❌ Error storing agent memory: {e}")
                self.logger.error(f"Error storing agent memory: {e}")
        
        if accepted > 0 or rejected > 0:
            print(f"   Memory summary: {accepted} accepted, {rejected} rejected")
    
    def _get_manual_memories_for_agent(self, turn: int) -> Optional[str]:
        """
        Retrieve and format manual memories for the agent.
        
        Returns formatted string of relevant memories or None if no memories found.
        """
        if not self.enable_manual_memory or not self.manual_memory:
            return None
        
        all_manual_memories = []
        
        # Get room memories
        if self.current_location:
            room_memories = self.manual_memory.get_memories_for_room(
                self.current_location, limit=6
            )
            all_manual_memories.extend(room_memories)
        
        # Get object memories (from inventory and visible objects)
        visible_objects = (
            self.last_extraction.visible_objects 
            if hasattr(self, 'last_extraction') and self.last_extraction and hasattr(self.last_extraction, 'visible_objects')
            else []
        )
        all_objects = list(set(self.current_inventory + visible_objects))
        if all_objects:
            object_memories = self.manual_memory.get_memories_for_objects(
                all_objects, per_object_limit=3, total_limit=6
            )
            all_manual_memories.extend(object_memories)
        
        # Get character memories
        visible_chars = (
            self.last_extraction.visible_characters 
            if hasattr(self, 'last_extraction') and self.last_extraction and hasattr(self.last_extraction, 'visible_characters')
            else []
        )
        if visible_chars:
            char_memories = self.manual_memory.get_memories_for_characters(
                visible_chars, per_character_limit=2, total_limit=4
            )
            all_manual_memories.extend(char_memories)
        
        # Mark as seen for recency tracking
        if all_manual_memories:
            self.manual_memory.mark_seen([m.id for m in all_manual_memories], turn)
        
        # Format memories for agent
        if not all_manual_memories:
            return None
        
        lines = ["🧠 MANUAL MEMORY NOTES (your explicit observations):"]
        
        for mem in all_manual_memories[:12]:  # Limit to 12 total
            # Format based on subject type
            subject_prefix = {
                "room": f"Room: {mem.subject_id.title()}",
                "object": f"Object: {mem.subject_id.title()}",
                "character": f"Character: {mem.subject_id.title()}"
            }.get(mem.subject_type, mem.subject_id.title())
            
            # Truncate content if too long
            content = mem.content if len(mem.content) <= 160 else mem.content[:157] + "..."
            
            # Build the line
            tags_str = f" [{', '.join(mem.tags)}]" if mem.tags else ""
            conf_str = f" (conf: {mem.confidence:.1f})" if mem.confidence < 0.9 else ""
            
            lines.append(f"  • {subject_prefix} — {content}{tags_str}{conf_str}")
        
        return "\n".join(lines)

    def get_action(self, current_game_state: str, history: list[tuple[str, str, int]], turn: int, user_input) -> tuple[str, str, str]:
        # Get memory recollection for current location (if memory is enabled)
        relevant_memories = None
        if self.enable_memory and self.current_location and self.action_memory:
            memory_recollection = self.action_memory.get_memory_recollection_for_agent(
                self.current_location
            )
            if memory_recollection:
                relevant_memories = memory_recollection
                print("\n" + "="*60)
                print("🧠 MEMORY RECOLLECTION")
                print("="*60)
                print(memory_recollection)
                print("="*60 + "\n")
        
        # Get manual memory notes (if enabled)
        manual_memories = self._get_manual_memories_for_agent(turn)
        if manual_memories:
            print("\n" + "="*60)
            print("🧠 AGENT MEMORY NOTES")
            print("="*60)
            print(manual_memories)
            print("="*60 + "\n")
            
            # Append to relevant memories
            if relevant_memories:
                relevant_memories += "\n\n" + manual_memories
            else:
                relevant_memories = manual_memories
        
        # Get strategic analysis from analyst (refreshes every k turns, shown every turn)
        if self.enable_analyst and self.analyst:
            analyst_insights = self.analyst.get_current_analysis_for_agent()
            if analyst_insights:
                print("\n" + analyst_insights + "\n")
                # Append to relevant memories for agent
                if relevant_memories:
                    relevant_memories += "\n\n" + analyst_insights
                else:
                    relevant_memories = analyst_insights
        
        # Get agent action with reasoning and expected outcome
        agent_response = self.agent.get_action_with_reasoning(
            game_state_text=current_game_state,
            previous_actions_and_responses=self.history[
                -self.agent_history_limit:
            ] if len(self.history) > 0 else [],  # Last N actions (configurable)
            action_counts=self.action_counts,
            relevant_memories=relevant_memories,
            user_input = user_input
        )

        agent_action = agent_response["action"]
        agent_reasoning = agent_response["reasoning"]
        agent_expected_outcome = agent_response.get("expected_outcome", None)
        agent_new_memories = agent_response.get("new_memories", [])
        
        # Process agent-proposed memories (if manual memory is enabled)
        if agent_new_memories and self.enable_manual_memory and self.manual_memory:
            self._process_agent_memories(agent_new_memories, turn)
        
        self.action_counts[agent_action] += 1
        return agent_action, agent_reasoning, agent_expected_outcome

    def _handle_memory_command(self, cmd: str) -> None:
        """Handle manual memory CLI commands."""
        parts = cmd.strip().split(maxsplit=1)
        if not parts:
            print("Memory commands: list, add, del, stats")
            return
        
        subcmd = parts[0].lower()
        
        if subcmd == "list":
            # List memories with optional filters
            args = parts[1] if len(parts) > 1 else ""
            filters = {}
            
            if "room:" in args:
                filters['subject_type'] = 'room'
                subject_id = args.split("room:")[1].split()[0] if "room:" in args else None
                if subject_id:
                    filters['subject_id'] = subject_id
            elif "object:" in args:
                filters['subject_type'] = 'object'
                subject_id = args.split("object:")[1].split()[0] if "object:" in args else None
                if subject_id:
                    filters['subject_id'] = subject_id
            elif "character:" in args:
                filters['subject_type'] = 'character'
                subject_id = args.split("character:")[1].split()[0] if "character:" in args else None
                if subject_id:
                    filters['subject_id'] = subject_id
            
            memories = self.manual_memory.list_memories(**filters)
            
            if memories:
                print(f"\n📝 Manual Memories ({len(memories)} found):")
                for mem in memories:
                    tags_str = f" [{', '.join(mem.tags)}]" if mem.tags else ""
                    print(f"  {mem.id[:8]}... | {mem.subject_type}:{mem.subject_id}")
                    print(f"    {mem.content}{tags_str}")
                    print(f"    (conf: {mem.confidence}, turn: {mem.updated_turn}, author: {mem.author})")
            else:
                print("No memories found")
        
        elif subcmd == "add":
            # Manually add a memory
            # Format: add room Kitchen "Floor is slippery" hazard,danger 0.8
            if len(parts) < 2:
                print("Usage: cmd:mem add <type> <id> \"<content>\" [tags] [confidence]")
                return
            
            args_parts = parts[1].split()
            if len(args_parts) < 3:
                print("Usage: cmd:mem add <type> <id> \"<content>\" [tags] [confidence]")
                return
            
            subject_type = args_parts[0]
            subject_id = args_parts[1]
            
            # Extract content (in quotes)
            content_match = re.search(r'"([^"]*)"', parts[1])
            if not content_match:
                print("Content must be in quotes")
                return
            content = content_match.group(1)
            
            # Extract tags and confidence
            remaining = parts[1].split('"')[-1].strip().split()
            tags = remaining[0].split(',') if remaining else []
            confidence = float(remaining[1]) if len(remaining) > 1 else 0.7
            
            try:
                note = self.manual_memory.add_memory(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    content=content,
                    tags=tags,
                    confidence=confidence,
                    author='user',
                    turn=self.turn
                )
                print(f"✅ Memory created: {note.id[:8]}...")
            except ValueError as e:
                print(f"❌ Error: {e}")
        
        elif subcmd == "del":
            # Delete a memory by ID
            if len(parts) < 2:
                print("Usage: cmd:mem del <memory_id>")
                return
            
            mem_id = parts[1].strip()
            # Support partial IDs (first 8 chars)
            if len(mem_id) == 8:
                all_mems = self.manual_memory.list_memories()
                matching = [m for m in all_mems if m.id.startswith(mem_id)]
                if len(matching) == 1:
                    mem_id = matching[0].id
                elif len(matching) > 1:
                    print(f"Ambiguous ID, matches {len(matching)} memories")
                    return
                elif len(matching) == 0:
                    print(f"No memory found with ID starting with {mem_id}")
                    return
            
            if self.manual_memory.delete_memory(mem_id):
                print(f"✅ Memory deleted: {mem_id[:8]}...")
            else:
                print(f"❌ Memory not found: {mem_id[:8]}...")
        
        elif subcmd == "stats":
            # Show memory statistics
            all_mems = self.manual_memory.list_memories()
            room_mems = [m for m in all_mems if m.subject_type == 'room']
            obj_mems = [m for m in all_mems if m.subject_type == 'object']
            char_mems = [m for m in all_mems if m.subject_type == 'character']
            agent_mems = [m for m in all_mems if m.author == 'agent']
            user_mems = [m for m in all_mems if m.author == 'user']
            
            print(f"\n📊 Memory Statistics:")
            print(f"  Total: {len(all_mems)} memories")
            print(f"  By type: {len(room_mems)} rooms, {len(obj_mems)} objects, {len(char_mems)} characters")
            print(f"  By author: {len(agent_mems)} agent, {len(user_mems)} user")
            
            # Tag stats
            from collections import Counter
            tag_counts = Counter()
            for mem in all_mems:
                tag_counts.update(mem.tags)
            if tag_counts:
                print(f"  Top tags: {', '.join(f'{tag}({count})' for tag, count in tag_counts.most_common(5))}")
        
        else:
            print(f"Unknown memory command: {subcmd}")
            print("Available: list, add, del, stats")

    def user_command_loop(self):
        if not self.user_commands_enabled:
            return None
        user_input = input("Message to llm or cmd:command: ")
        while user_input.startswith("cmd:"):
            #parse and execute command
            user_input = user_input.replace("cmd:", "")
            
            # Handle save/load game state commands
            if user_input.startswith("savestate"):
                # Save complete game state
                parts = user_input.split(maxsplit=1)
                save_name = parts[1] if len(parts) > 1 else f"manual_save_{int(time.time())}"
                self.save_game_state(save_name)
            elif user_input.startswith("loadstate"):
                # Load complete game state
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1:
                    self.load_game_state(parts[1])
                else:
                    print("Usage: cmd:loadstate <save_name>")
            elif user_input.startswith("liststates"):
                # List available saves
                if self.saves_dir.exists():
                    saves = [d.name for d in self.saves_dir.iterdir() if d.is_dir()]
                    if saves:
                        print("\n📁 Available saves:")
                        for save in sorted(saves):
                            metadata_file = self.saves_dir / save / "metadata.json"
                            if metadata_file.exists():
                                with open(metadata_file, "r") as f:
                                    metadata = json.load(f)
                                print(f"  - {save}: Turn {metadata.get('turn')}, Score {metadata.get('current_score')} (saved {metadata.get('saved_at')})")
                            else:
                                print(f"  - {save}")
                    else:
                        print("No saves found")
                else:
                    print("No saves directory found")
            elif user_input.startswith("mem ") and self.enable_manual_memory and self.manual_memory:
                # Manual memory commands
                self._handle_memory_command(user_input[4:])  # Skip "mem "
            elif user_input.startswith("save"):
                # Legacy Zork save command
                next_state=self.zork.send_interactive_command("save", user_input.replace("save ", ""), "Please enter a filename", "Ok.")
                print(f"Next state after USER COMMAND: {next_state}")
            elif user_input.startswith("restore"):
                # Legacy Zork restore command
                next_state=self.zork.send_interactive_command("restore", user_input.replace("restore ", ""), "Please enter a filename", "Ok.")
                print(f"Next state after USER COMMAND: {next_state}")
            else:
                # Generic Zork command
                next_state = self.zork.send_command(user_input)
                print(f"Next state after USER COMMAND: {next_state}")
            
            # read next input
            user_input = input("\nMessage to llm or cmd:command: ")
        return user_input



    def run(self, state_to_load : str | None = None) -> int:
        """Run a minimal gameplay loop.

        Returns:
            Final Zork score at the end of the episode.
        """
        print("Starting Zork testbed...")
        print("Starting a new loop")
        
        # Display loaded memory summary at episode start (if memory is enabled)
        if self.enable_memory and self.action_memory:
            memory_summary = self.action_memory.get_memory_load_summary()
            print("\n" + memory_summary + "\n")
        
        # Display global game knowledge summary
        if self.enable_analyst and self.analyst:
            knowledge_summary = self.analyst.get_knowledge_summary()
            print("\n" + knowledge_summary + "\n")

        last_command = None
        last_room = None
        expected_outcome = None

        with self.zork:
            # Start the game and switch to verbose mode for richer state
            current_state = self.zork.start()
            self.zork.send_command("verbose")

            turns_from_combat = 0

            if state_to_load:
                self.load_game_state(state_to_load)
                current_state = self.zork.send_command("look")

            # Initial extraction of state
            if self.enable_extractor and self.extractor:
                try:
                    extracted: ExtractorResponse | None = self.extractor.extract_info(
                        current_state
                    )
                    if extracted:
                        print(f"Initial location: {extracted.current_location_name}")
                        if extracted.score is not None:
                            self.current_score = extracted.score
                except Exception:
                    extracted = None
            else:
                extracted = None

            # Determine starting turn (1 for new game, or loaded turn for restored game)
            start_turn = self.turn if self.turn > 0 else 1
            
            if start_turn > 1:
                print(f"\n🔄 Resuming gameplay from turn {start_turn}...\n")
            
            for self.turn in range(start_turn, self.max_turns + 1):

                # Extract information from current state (if extractor enabled)
                if self.enable_extractor and self.extractor:
                    extraction_response = self.extractor.extract_info(
                        current_state,
                        previous_location = last_room,
                        previous_action = last_command,
                        expected_outcome = expected_outcome
                    )
                    # Store extraction for manual memory retrieval
                    self.last_extraction = extraction_response
                else:
                    extraction_response = None
                    self.last_extraction = None
                
                if extraction_response and extraction_response.in_combat:
                    turns_from_combat = 0
                else:
                    turns_from_combat += 1

                self.current_score, self.max_score = self.zork.score()

                if extraction_response:
                    # Update current location
                    if "Unknown Location" not in extraction_response.current_location_name:
                        self.current_location = extraction_response.current_location_name                        
                    else:
                        self.current_location = last_room
                    
                    # Update inventory if available
                    if hasattr(extraction_response, 'inventory') and extraction_response.inventory:
                        self.current_inventory = extraction_response.inventory
                    
                
                    
                    # print(f"Extraction response: {extraction_response}")
                    
                    # Record room visit with entities in spatial memory (if memory is enabled)
                    if self.enable_memory and self.action_memory:
                        visible_objects = extraction_response.visible_objects if extraction_response else []
                        visible_characters = extraction_response.visible_characters if extraction_response else []
                        self.action_memory.record_room_visit(
                            room_name=self.current_location,
                            objects=visible_objects,
                            characters=visible_characters
                        )

                        if last_room and last_command:
                            self.previous_location = last_room
                            outcome = self.action_memory.record_action_outcome(
                                location=self.current_location,
                                action=last_command,
                                game_response=next_state,
                                turn_number=self.turn,
                                extractor_response=extraction_response,
                                previous_score=self.previous_score,
                                current_score=self.current_score,
                                previous_location=self.previous_location,
                                inventory_changes=[]  # Could extract from inventory comparison
                            )
                            # Print action outcome
                            print(f"🎯 Action outcome: {outcome.outcome_type.value}")
                            if outcome.score_change != 0:
                                print(f"   Score change: {outcome.score_change:+d}")
                            if outcome.location_changed:
                                print(f"   Moved to: {outcome.new_location}")

                        
                        # Print spatial memory insights
                        room_summary = self.action_memory.spatial_memory.get_room_summary(self.current_location)
                        if room_summary.get("has_data"):
                            print(f"📍 Room visits: {room_summary['current_episode_visits']} (total: {room_summary['total_visits']})")
                            if room_summary.get('objects'):
                                print(f"   Objects seen: {', '.join(room_summary['objects'][:5])}")
                            if room_summary.get('characters'):
                                print(f"   Characters: {', '.join(room_summary['characters'])}")


                # Update last_room to the resolved current location (not the raw extraction)
                # This ensures we don't use "Unknown Location" as previous_location
                last_room = self.current_location
                
                # Check if we should run strategic analysis
                if self.enable_analyst and self.analyst and self.analyst.should_run_analysis(self.turn):
                    try:
                        print("\n" + "🔍" * 35)
                        print("🔍 RUNNING STRATEGIC ANALYSIS...")
                        print("🔍" * 35)
                        
                        # Run the analysis
                        analysis_report = self.analyst.analyze_gameplay(
                            current_turn=self.turn,
                            action_history=self.history[-20:],
                            current_location=self.current_location,
                            current_inventory=self.current_inventory,
                            current_score=self.current_score,
                        )
                        
                        print("\n" + "=" * 70)
                        print("📊 STRATEGIC ANALYSIS COMPLETED")
                        print(f"   Goals: {len(analysis_report.strategic_goals)}")
                        print(f"   Unsolved Puzzles: {len(analysis_report.unsolved_puzzles)}")
                        print(f"   Hypotheses: {len(analysis_report.progress_hypotheses)}")
                        print(f"   Recommendations: {len(analysis_report.recommended_actions)}")
                        print("=" * 70 + "\n")
                        
                    except Exception as e:
                        self.logger.error(f"Strategic analysis failed: {e}")
                        print(f"⚠️ Strategic analysis failed: {e}\n")



                #self.current_score, self.max_score = self.zork.score()
                print(f"Turn {self.turn} score: {self.current_score} \n{current_state}")

                # Auto-save on turns where turn % 10 == 9 (i.e., turns 9, 19, 29, 39, ...)
                if self.turn % 10 == 9:
                    self.auto_save()

                user_input = self.user_command_loop()

                action, reasoning, expected_outcome = self.get_action(current_state, self.history, self.turn
                , user_input)
                print(f"Reasoning: {reasoning}")
                self.tts.speak(reasoning, "", voice_type="agent")
                print(f"\n\nAction: {action}\n\n")
                self.tts.speak(action, "", voice_type="agent")
                print(f"Expected outcome: {expected_outcome}")
                # self.tts.speak(expected_outcome, "Expectation is the following.", voice_type="agent")


                # Store previous state for memory recording
                self.previous_score = self.current_score
                self.previous_location = self.current_location
                last_command = action

                next_state = self.zork.send_command(action)
                print(f"Next state: {next_state}")
                self.tts.speak(next_state, "", voice_type="game")

                
                

                # Check game over based on the response
                self.game_over, self.reason = self.zork.is_game_over(next_state)
                if self.game_over:
                    # Capture final score from the last response if possible
                    try:
                        self.current_score, self.max_score = self.zork.score(next_state)
                    except Exception:
                        pass

                    print(f"\n💀 GAME OVER on turn {self.turn}: {self.reason}")
                    print(f"Final score: {self.current_score} / {self.max_score}")
                    
                    # Record death outcome if it was a death (not just game completion)
                    is_death = self._is_death_condition(self.reason, next_state)
                    if is_death and self.current_location and self.enable_memory and self.action_memory:
                        print(f"⚠️  Recording death at location: {self.current_location}")
                        
                        # Mark the location as dangerous in spatial memory
                        if self.action_memory.spatial_memory and self.current_location in self.action_memory.spatial_memory.rooms:
                            room = self.action_memory.spatial_memory.rooms[self.current_location]
                            room.is_dangerous = True
                            room.notes.append(f"DEATH: {self.reason} (Turn {self.turn})")
                            print(f"   ⚠️  Location marked as DANGEROUS")
                        
                        # Record the death-causing action as a critical failure
                        if self.enable_extractor and self.extractor:
                            next_extraction = self.extractor.extract_info(
                                next_state,
                                previous_location = last_room,
                                previous_action = last_command,
                                expected_outcome = expected_outcome
                            )
                        else:
                            next_extraction = None
                        death_outcome = self.action_memory.record_action_outcome(
                            location=self.current_location,
                            action=f"[DEATH] {action}",
                            game_response=f"DEATH: {self.reason}\n{next_state}",
                            turn_number=self.turn,
                            extractor_response=next_extraction,
                            previous_score=self.previous_score,
                            current_score=self.current_score,
                            previous_location=self.previous_location,
                            inventory_changes=[]
                        )
                        print(f"   💀 Death outcome recorded: {death_outcome.outcome_type.value}")
                    
                    # Save memory at episode end (CRITICAL: save before exiting)
                    if self.enable_memory and self.action_memory:
                        print("\n💾 Saving memory to disk (including death information)...")
                        try:
                            self.action_memory.save_to_disk()
                            print("✅ Memory saved successfully")
                        except Exception as e:
                            print(f"❌ ERROR saving memory: {e}")
                            self.logger.error(f"Failed to save memory on death: {e}")
                        
                        self._print_memory_summary()
                    
                    # Save manual memory at episode end
                    if self.enable_manual_memory and self.manual_memory:
                        print("💾 Saving manual memories to disk...")
                        try:
                            self.manual_memory.save_to_disk()
                            print("✅ Manual memories saved successfully")
                        except Exception as e:
                            print(f"❌ ERROR saving manual memories: {e}")
                            self.logger.error(f"Failed to save manual memories on death: {e}")
                    
                    return self.current_score

                # Track history with agent thinking (reasoning and expected outcome)
                self.history.append({
                    "action": action,
                    "response": next_state,
                    "score": self.current_score,
                    "reasoning": reasoning,
                    "expected_outcome": expected_outcome or ""
                })

                

                current_state = next_state

                if self.turn_delay_seconds > 0:
                    time.sleep(self.turn_delay_seconds)

                # TODO wait for user input
                #self.user_input = input("Enter a command: ")

            print("Max turns reached.")
            print(f"Final score: {self.current_score} / {self.max_score}")
            
            # Save memory at episode end (if memory is enabled)
            if self.enable_memory and self.action_memory:
                print("\n💾 Saving memory to disk...")
                self.action_memory.save_to_disk()
                self._print_memory_summary()
            
            # Save manual memory at episode end
            if self.enable_manual_memory and self.manual_memory:
                print("💾 Saving manual memories to disk...")
                self.manual_memory.save_to_disk()
            
            return self.current_score
    
    def _is_death_condition(self, reason: str, game_text: str) -> bool:
        """
        Check if game over was due to death (vs. game completion or other end).
        
        Args:
            reason: Game over reason from is_game_over()
            game_text: Current game text
        
        Returns:
            True if this was a death, False otherwise
        """
        death_indicators = [
            "death",
            "died",
            "dead",
            "killed",
            "eaten",
            "grue",
            "crushed",
            "blown up",
            "drowned",
            "suffocated",
            "perished",
            "demise",
            "fatal",
            "game over",  # Often indicates death in Zork
        ]
        
        # Check reason and game text for death indicators
        combined_text = (reason + " " + game_text).lower()
        return any(indicator in combined_text for indicator in death_indicators)
    
    def _print_memory_summary(self) -> None:
        """Print a summary of what was learned during the episode."""
        if not self.enable_memory or not self.action_memory:
            return
        
        print("\n" + "="*60)
        print("📊 MEMORY SUMMARY")
        print("="*60)
        
        # Action memory summary
        print(f"\n📍 Locations explored: {len(self.action_memory.location_patterns)}")
        print(f"🎬 Actions recorded: {len(self.action_memory.current_episode_outcomes)}")
        
        # Top locations by activity
        if self.action_memory.location_patterns:
            print("\n🏆 Most active locations:")
            sorted_locations = sorted(
                self.action_memory.location_patterns.items(),
                key=lambda x: x[1].total_actions_recorded,
                reverse=True
            )
            for location, patterns in sorted_locations[:5]:
                print(f"   {location}: {patterns.total_actions_recorded} actions")
                if patterns.reliable_actions:
                    print(f"      ✅ Reliable: {', '.join(list(patterns.reliable_actions)[:3])}")
                if patterns.failed_actions:
                    print(f"      ❌ Failed: {', '.join(list(patterns.failed_actions)[:3])}")
        
        # Spatial memory summary
        if self.action_memory.spatial_memory:
            print(f"\n🗺️  Rooms visited: {len(self.action_memory.spatial_memory.rooms)}")
            
            # Count total entities
            total_objects = sum(
                len(room.objects_observed) 
                for room in self.action_memory.spatial_memory.rooms.values()
            )
            total_characters = sum(
                len(room.characters_observed) 
                for room in self.action_memory.spatial_memory.rooms.values()
            )
            print(f"📦 Unique objects found: {total_objects}")
            print(f"👥 Unique characters met: {total_characters}")
            
            # Most visited rooms
            if self.action_memory.spatial_memory.rooms:
                print("\n🔄 Most visited rooms:")
                sorted_rooms = sorted(
                    self.action_memory.spatial_memory.rooms.items(),
                    key=lambda x: x[1].current_episode_visits,
                    reverse=True
                )
                for room_name, room in sorted_rooms[:5]:
                    print(f"   {room_name}: {room.current_episode_visits} visits")
                    if room.objects_observed:
                        print(f"      Objects: {', '.join(list(room.objects_observed.keys())[:3])}")
        
        # Navigation insights
        if self.current_location and self.action_memory.spatial_memory:
            accessible = self.action_memory.get_accessible_rooms(
                self.current_location,
                max_distance=2
            )
            if accessible:
                print(f"\n🧭 Accessible from {self.current_location}:")
                for room, activation in list(accessible.items())[:5]:
                    print(f"   {room}: {activation:.2f} activation")
        
        print("\n" + "="*60)


if __name__ == "__main__":
    # Example usage:
    # With memory: testbed = ZorkTestbed(user_commands_enabled=False, episode_id="my_episode_006", enable_memory=True)
    # Without memory: testbed = ZorkTestbed(user_commands_enabled=False, episode_id="my_episode_006", enable_memory=False)
    # With user commands enabled (for manual save/load): testbed = ZorkTestbed(user_commands_enabled=True, ...)
    # With TTS enabled: testbed = ZorkTestbed(..., enable_tts=True)
    # With manual memory enabled (default): testbed = ZorkTestbed(..., enable_manual_memory=True)
    # Without manual memory: testbed = ZorkTestbed(..., enable_manual_memory=False)
    # With extractor enabled (default): testbed = ZorkTestbed(..., enable_extractor=True)
    # Without extractor: testbed = ZorkTestbed(..., enable_extractor=False)

    testbed = ZorkTestbed(
        user_commands_enabled=False,
        episode_id="my_episode_009",
        enable_memory=False,
        enable_analyst=False,
        enable_tts=False,
        enable_manual_memory=True,
        enable_extractor=True)
    
    # To load a previous save before starting:
    
    
    # Run the game (auto-saves on turns 1, 11, 21, 31, ...)
    testbed.run(state_to_load="autosave_turn_208")
    # testbed.run()