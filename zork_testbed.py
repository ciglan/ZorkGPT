"""Minimal Zork testbed.

This testbed runs a very simple agent against the Zork interface without any
knowledge base, critic, or information extractor. It demonstrates how to:

- Start the Zork process and obtain the initial state
- Send actions and read responses each turn
- Track and print score and termination conditions

Take inspiration from `zork_orchestrator_simple.py` but keep everything
lightweight and self-contained for quick experimentation.


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
import random
import time

from config import get_client_api_key, get_config
from hybrid_zork_extractor import ExtractorResponse, HybridZorkExtractor
from llm_client import LLMClientWrapper
from logger import setup_logging
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
    def __init__(self, max_turns: int = 6000, turn_delay_seconds: float = 0.25) -> None:
        config = get_config()

        self.episode_log_file = config.files.episode_log_file
        self.json_log_file = config.files.json_log_file
        self.logger = setup_logging(self.episode_log_file, self.json_log_file)

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
        self.extractor = HybridZorkExtractor(
            client=extractor_client, logger=self.logger
        )

        self.max_turns = max_turns
        self.turn_delay_seconds = turn_delay_seconds
        # self.agent = SimpleAgent()

        self.zork = ZorkInterface(timeout=1.0)
        self.history: list[tuple[str, str]] = []
        self.current_score = 0
        self.max_score = 0
        self.current_state = ""
        self.next_state = ""
        self.turn = 0
        self.game_over = False
        self.reason = ""
        self.user_input = ""
        self.turn_extracted = None
        self.turn_extracted_score = 0
        self.turn_extracted_max_score = 0
        self.action_counts = Counter()


    def get_action(self, current_game_state: str, history: list[tuple[str, str]], turn: int) -> tuple[str, str]:
        # Get agent action with reasoning
            agent_response = self.agent.get_action_with_reasoning(
                game_state_text=current_game_state,
                previous_actions_and_responses=self.history[
                    -42:
                ],  # Last 42 actions
                action_counts=self.action_counts,
                relevant_memories=None,
            )

            agent_action = agent_response["action"]
            agent_reasoning = agent_response["reasoning"]
            self.action_counts[agent_action] += 1
            return agent_action, agent_reasoning

    def run(self) -> int:
        """Run a minimal gameplay loop.

        Returns:
            Final Zork score at the end of the episode.
        """
        print("Starting Zork testbed...")
        print("Starting a new loop")

        with self.zork:
            # Start the game and switch to verbose mode for richer state
            current_state = self.zork.start()
            self.zork.send_command("verbose")

            

            # Initial extraction of state
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

            for self.turn in range(1, self.max_turns + 1):

                self.current_score, self.max_score = self.zork.score()
                print(f"Turn {self.turn} score: {self.current_score} \n{current_state}")
                action, reasoning = self.get_action(current_state, self.history, self.turn
                )
                print(f"Reasoning: {reasoning}")
                print(f"\n\nAction: {action}\n\n")
                next_state = self.zork.send_command(action)
                print(f"Next state: {next_state}")

                # Check game over based on the response
                self.game_over, self.reason = self.zork.is_game_over(next_state)
                if self.game_over:
                    # Capture final score from the last response if possible
                    try:
                        self.current_score, self.max_score = self.zork.score(next_state)
                    except Exception:
                        pass

                    print(f"Game over on turn {self.turn}: {self.reason}")
                    print(f"Final score: {self.current_score} / {self.max_score}")
                    return self.current_score

                # Track history and score; attempt to update score from the process
                self.history.append((action, next_state))

                # Extract structured info each turn, fall back to raw scoring
             
                # print(f"[Turn {self.turn}] Extracting info")
                # self.turn_extracted = self.extractor.extract_info(next_state)
                # print(f"[Turn {self.turn}] {self.turn_extracted=}")
                # if self.turn_extracted:
                #     # Prefer extractor score if available
                #     if self.turn_extracted.score is not None:
                #         self.current_score = self.turn_extracted.score
                #         self.max_score = self.max_score or 585
                #     # Print a short structured snapshot for visibility
                #     loc = self.turn_extracted.current_location_name
                #     exits = (
                #         ", ".join(self.turn_extracted.exits)
                #         if self.turn_extracted.exits
                #         else "-"
                #     )

                current_state = next_state

                if self.turn_delay_seconds > 0:
                    time.sleep(self.turn_delay_seconds)

                # TODO wait for user input
                #self.user_input = input("Enter a command: ")

            print("Max turns reached.")
            print(f"Final score: {self.current_score} / {self.max_score}")
            return self.current_score


if __name__ == "__main__":
    testbed = ZorkTestbed()
    testbed.run()
