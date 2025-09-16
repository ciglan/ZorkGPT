"""Minimal Zork testbed.

This testbed runs a very simple agent against the Zork interface without any
knowledge base, critic, or information extractor. It demonstrates how to:

- Start the Zork process and obtain the initial state
- Send actions and read responses each turn
- Track and print score and termination conditions

Take inspiration from `zork_orchestrator_simple.py` but keep everything
lightweight and self-contained for quick experimentation.
"""

from __future__ import annotations

import time
import random
from typing import List, Tuple, Optional

from zork_api import ZorkInterface
from hybrid_zork_extractor import HybridZorkExtractor, ExtractorResponse


class SimpleAgent:
    """A minimal, stateless agent that issues basic Zork commands.

    The agent is intentionally simple: it alternates between looking around,
    trying to take items, checking inventory, and moving in random directions.
    This is meant as a starting point for experimentation.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self.random = random.Random(seed)

        self.movement_actions: List[str] = [
            "north",
            "south",
            "east",
            "west",
            "up",
            "down",
        ]

        self.utility_actions: List[str] = [
            "look",
            "take all",
            "inventory",
        ]

    def choose_action(
        self, game_state_text: str, history: List[Tuple[str, str]], turn: int
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


def run_testbed(max_turns: int = 60, turn_delay_seconds: float = 0.25) -> int:
    """Run a minimal gameplay loop with the `SimpleAgent`.

    Args:
        max_turns: Maximum number of turns to play.
        turn_delay_seconds: Delay between turns for readability.

    Returns:
        Final Zork score at the end of the episode.
    """
    agent = SimpleAgent()
    extractor = HybridZorkExtractor()

    with ZorkInterface(timeout=1.0) as zork:
        # Start the game and switch to verbose mode for richer state
        current_state = zork.start()
        zork.send_command("verbose")

        current_score, max_score = zork.score(current_state)

        # Initial extraction of state
        try:
            extracted: ExtractorResponse | None = extractor.extract_info(current_state)
            if extracted:
                print(f"Initial location: {extracted.current_location_name}")
                if extracted.score is not None:
                    current_score = extracted.score
        except Exception:
            extracted = None

        history: List[Tuple[str, str]] = []

        for turn in range(1, max_turns + 1):
            action = agent.choose_action(current_state, history, turn)
            next_state = zork.send_command(action)

            # Check game over based on the response
            game_over, reason = zork.is_game_over(next_state)
            if game_over:
                # Capture final score from the last response if possible
                try:
                    current_score, max_score = zork.score(next_state)
                except Exception:
                    pass

                print(f"Game over on turn {turn}: {reason}")
                print(f"Final score: {current_score} / {max_score}")
                return current_score

            # Track history and score; attempt to update score from the process
            history.append((action, next_state))
            
            # Extract structured info each turn, fall back to raw scoring
            try:
                turn_extracted = extractor.extract_info(next_state)
                if turn_extracted:
                    # Prefer extractor score if available
                    if turn_extracted.score is not None:
                        current_score = turn_extracted.score
                        max_score = max_score or 585
                    # Print a short structured snapshot for visibility
                    loc = turn_extracted.current_location_name
                    exits = ", ".join(turn_extracted.exits) if turn_extracted.exits else "-"
                    print(f"[Turn {turn}] {turn_extracted=}")
            except Exception:
                # Fallback to process score when extraction fails
                try:
                    current_score, max_score = zork.score()
                except Exception:
                    pass

            current_state = next_state

            if turn_delay_seconds > 0:
                time.sleep(turn_delay_seconds)

            # TODO wait for use input

        print("Max turns reached.")
        print(f"Final score: {current_score} / {max_score}")
        return current_score


if __name__ == "__main__":
    run_testbed()