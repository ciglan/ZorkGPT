"""
Game Analyst module for strategic gameplay analysis.

Analyzes game history to:
- Formulate strategic goals
- Track unutilized objects
- Generate hypotheses for progress
- Build global knowledge base of successful patterns
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeEntry:
    """A single piece of game knowledge."""
    action_pattern: str  # e.g., "take leaflet from mailbox"
    context: str  # e.g., "West of House, mailbox visible"
    outcome: str  # What happened
    score_gain: int  # Points gained (0 if none)
    category: str  # "puzzle_solution", "item_collection", "navigation", "progression"
    location: str | None = None  # Where this works
    prerequisites: list[str] = field(default_factory=list)  # What's needed first
    times_confirmed: int = 1  # How many times this was observed
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeEntry":
        """Create from dictionary."""
        return cls(**data)
    
    def matches(self, other: "KnowledgeEntry") -> bool:
        """Check if this entry matches another (for deduplication)."""
        return (
            self.action_pattern.lower() == other.action_pattern.lower() and
            self.category == other.category and
            (self.location is None or other.location is None or 
             self.location.lower() == other.location.lower())
        )


@dataclass
class AnalysisReport:
    """Structured analysis report from the game analyst."""
    
    turn: int
    strategic_goals: list[str]
    unsolved_puzzles: list[dict[str, str]]  # {puzzle, location, description}
    progress_hypotheses: list[str]
    key_observations: list[str]
    recommended_actions: list[str]
    full_report: str  # Full text report for agent
    new_knowledge: list[KnowledgeEntry] = field(default_factory=list)  # Newly discovered knowledge


class GameAnalyst:
    """
    Analyzes gameplay history to provide strategic insights and goals.
    
    Runs periodically to evaluate:
    - What objects have been encountered but not used effectively
    - What goals should be prioritized
    - What hypotheses might lead to progress
    """
    
    def __init__(
        self,
        client,
        model: str | None = None,
        analysis_interval: int = 10,
        knowledge_file: str = "game_knowledge.json",
        logger: logging.Logger | None = None
    ):
        """
        Initialize the game analyst.
        
        Args:
            client: LLM client for analysis
            model: Model to use for analysis (defaults to config.llm.analysis_model if None)
            analysis_interval: How many turns between analyses
            knowledge_file: Path to persistent knowledge base file
            logger: Logger instance
        """
        self.client = client
        # Default to config if model not provided
        if model is None:
            from config import get_config
            config = get_config()
            model = config.llm.analysis_model
        self.model = model
        self.analysis_interval = analysis_interval
        self.knowledge_file = Path(knowledge_file)
        self.logger = logger or logging.getLogger("zorkgpt")
        
        # Track analysis state
        self.last_analysis_turn = 0
        self.current_analysis: AnalysisReport | None = None
        
        # Global knowledge base
        self.knowledge_base: list[KnowledgeEntry] = []
        self._load_knowledge_base()
        
    def should_run_analysis(self, current_turn: int) -> bool:
        """Check if analysis should run this turn."""
        if current_turn == 0:
            return False
        
        turns_since_last = current_turn - self.last_analysis_turn
        return turns_since_last >= self.analysis_interval
    
    def analyze_gameplay(
        self,
        current_turn: int,
        action_history: list[dict],
        current_location: str,
        current_inventory: list[str],
        current_score: int,
    ) -> AnalysisReport:
        """
        Perform comprehensive gameplay analysis.
        
        Args:
            current_turn: Current turn number
            action_history: List of history dicts with action, response, score, reasoning, expected_outcome
            current_location: Current room
            current_inventory: Current inventory items
            current_score: Current game score
            
        Returns:
            AnalysisReport with strategic insights
        """
        self.logger.info(
            f"🔍 Running gameplay analysis at turn {current_turn}",
            extra={"turn": current_turn}
        )
        
        # Prepare context for analysis
        analysis_context = self._prepare_analysis_context(
            current_turn=current_turn,
            action_history=action_history,
            current_location=current_location,
            current_inventory=current_inventory,
            current_score=current_score,
        )
        
        # Generate analysis via LLM
        analysis_report = self._generate_analysis(analysis_context, current_turn)
        
        # Update state
        self.last_analysis_turn = current_turn
        self.current_analysis = analysis_report
        
        # Add new knowledge to global knowledge base
        if analysis_report.new_knowledge:
            for new_entry in analysis_report.new_knowledge:
                self._add_knowledge_if_new(new_entry)
            self._save_knowledge_base()
            self.logger.info(
                f"📚 Added {len(analysis_report.new_knowledge)} new knowledge entries",
                extra={"turn": current_turn, "new_knowledge_count": len(analysis_report.new_knowledge)}
            )
        
        self.logger.info(
            f"✅ Analysis complete: {len(analysis_report.strategic_goals)} goals, "
            f"{len(analysis_report.unsolved_puzzles)} unsolved puzzles, "
            f"knowledge base: {len(self.knowledge_base)} entries",
            extra={
                "turn": current_turn,
                "goals_count": len(analysis_report.strategic_goals),
                "unsolved_puzzles_count": len(analysis_report.unsolved_puzzles),
                "knowledge_base_size": len(self.knowledge_base),
            }
        )
        
        return analysis_report
    
    def get_current_analysis_for_agent(self) -> str:
        """Get formatted analysis for agent prompt."""
        if not self.current_analysis:
            return ""
        
        lines = []
        lines.append("=" * 70)
        lines.append("🎯 STRATEGIC ANALYSIS")
        lines.append(f"(Analysis from turn {self.current_analysis.turn})")
        lines.append("=" * 70)
        lines.append("")
        
        # Include relevant knowledge from global knowledge base
        # if self.knowledge_base:
        #     lines.append("📚 GAME KNOWLEDGE (from all episodes):")
        #     # Show most relevant knowledge (by times confirmed)
        #     sorted_knowledge = sorted(self.knowledge_base, key=lambda k: k.times_confirmed, reverse=True)
        #     for entry in sorted_knowledge[:10]:  # Top 10 most confirmed
        #         lines.append(f"  • {entry.action_pattern}")
        #         lines.append(f"    Context: {entry.context}")
        #         if entry.score_gain > 0:
        #             lines.append(f"    Score: +{entry.score_gain} points")
        #         if entry.prerequisites:
        #             lines.append(f"    Prerequisites: {', '.join(entry.prerequisites)}")
        #         lines.append(f"    Confirmed: {entry.times_confirmed}x")
        #     lines.append("")
        
        # Strategic goals
        if self.current_analysis.strategic_goals:
            lines.append("📋 STRATEGIC GOALS:")
            for i, goal in enumerate(self.current_analysis.strategic_goals, 1):
                lines.append(f"  {i}. {goal}")
            lines.append("")
        
        # Unsolved puzzles
        if self.current_analysis.unsolved_puzzles:
            lines.append("🔍 UNSOLVED PUZZLES:")
            for puzzle_info in self.current_analysis.unsolved_puzzles[:10]:  # Top 10
                puzzle_name = puzzle_info.get('puzzle', 'Unknown')
                location = puzzle_info.get('location', 'Unknown')
                description = puzzle_info.get('description', 'Not analyzed')
                lines.append(f"  • {puzzle_name} (at {location})")
                lines.append(f"    → {description}")
            lines.append("")
        
        # Progress hypotheses
        if self.current_analysis.key_observations:
            lines.append("💡 KEY OBSERVATIONS:")
            for i, hypothesis in enumerate(self.current_analysis.key_observations, 1):
                lines.append(f"  {i}. {hypothesis}")
            lines.append("")
        
 
        
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def _prepare_analysis_context(
        self,
        current_turn: int,
        action_history: list[dict],
        current_location: str,
        current_inventory: list[str],
        current_score: int,
    ) -> str:
        """Prepare context for LLM analysis."""
        lines = []
        
        # Basic state
        lines.append(f"CURRENT STATE (Turn {current_turn}):")
        lines.append(f"- Location: {current_location}")
        lines.append(f"- Score: {current_score}")
        lines.append(f"- Inventory: {', '.join(current_inventory) if current_inventory else 'Empty'}")
        lines.append("")
        
        # Recent action history with scores and reasoning
        lines.append("RECENT ACTION HISTORY (Last 15 actions):")
        prev_score = action_history[-16]["score"] if len(action_history) >= 16 else 0
        for entry in action_history[-15:]:
            action = entry["action"]
            response = entry["response"]
            score = entry["score"]
            reasoning = entry.get("reasoning", "")
            expected_outcome = entry.get("expected_outcome", "")
            
            score_change = score - prev_score
            score_indicator = f" [Score: {score}" + (f", +{score_change}]" if score_change > 0 else "]")
            lines.append(f"  Action: {action}{score_indicator}")
            
            # Include agent's reasoning if available
            if reasoning:
                lines.append(f"  Agent's thinking: {reasoning[:150]}...")
            if expected_outcome:
                lines.append(f"  Expected: {expected_outcome[:100]}...")
            
            # Truncate long responses
            response_preview = response[:200] + "..." if len(response) > 200 else response
            lines.append(f"  Result: {response_preview}")
            lines.append("")
            prev_score = score
        
        # Include knowledge base context
        if self.knowledge_base:
            lines.append("")
            lines.append("GLOBAL GAME KNOWLEDGE (successful patterns from all episodes):")
            for entry in self.knowledge_base[:15]:  # Top 15 entries
                lines.append(f"  - {entry.action_pattern} ({entry.category})")
                if entry.score_gain > 0:
                    lines.append(f"    → Gains {entry.score_gain} points")
                if entry.location:
                    lines.append(f"    → Works at: {entry.location}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _generate_analysis(self, context: str, current_turn: int) -> AnalysisReport:
        """Generate analysis via LLM."""
        prompt = f"""You are a strategic gameplay analyst for a Zork text adventure game. Analyze the gameplay history and provide strategic guidance.

{context}

Based on this gameplay history, provide a comprehensive strategic analysis:

1. **STRATEGIC GOALS**: What are the top 3-5 goals the player should focus on? Consider score potential, progress opportunities, and current capabilities.

2. **UNSOLVED PUZZLES**: What puzzles have been encountered recently but not solved? Provide a list of puzzles with their locations and descriptions. 
Based on current knowledge of the problem, puzzle, develop hypotheses for how to solve it. Consider objects in nearby loocations, also consider all objects that player was able to add to the inventory.


3. **KEY OBSERVATIONS**: What patterns or clues stand out from the gameplay?

4. **NEW KNOWLEDGE**: If you identify successful action patterns that advanced the game (especially those that gained points or opened new areas), describe them for the global knowledge base.

Format your response as:

STRATEGIC GOALS:
- [goal 1]
- [goal 2]
...

UNSOLVED PUZZLES:
- Potential Puzzle: [name], Location: [where seen], Reason: [why it matters and what can be done to solve it]
...

KEY OBSERVATIONS:
- [observation 1]
- [observation 2]
...

NEW_KNOWLEDGE:
- [Action pattern | Context | Outcome | Score gain | Category | Prerequisites (if any)]
  Example: take leaflet from mailbox | West of House | Gained possession of important clue | 5 | item_collection | mailbox must be open

Be specific, actionable, and focus on moves that could unlock progress or increase score."""

        try:
            messages = [{"role": "user", "content": prompt}]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )
            
            analysis_text = response.content.strip()
            
            # Parse the response
            return self._parse_analysis_response(analysis_text, current_turn)
            
        except Exception as e:
            self.logger.error(f"Analysis generation failed: {e}", extra={"turn": current_turn})
            # Return empty analysis on failure
            return AnalysisReport(
                turn=current_turn,
                strategic_goals=[],
                unsolved_puzzles=[],
                progress_hypotheses=[],
                key_observations=[],
                recommended_actions=[],
                full_report="Analysis generation failed."
            )
    
    def _parse_analysis_response(self, response: str, turn: int) -> AnalysisReport:
        """Parse LLM response into structured report."""
        lines = response.strip().split('\n')
        
        strategic_goals = []
        unsolved_puzzles = []
        progress_hypotheses = []
        key_observations = []
        recommended_actions = []
        new_knowledge = []
        
        current_section = None
        
        for line in lines:
            line = line.strip()
            
            # Detect sections
            if line.upper().startswith('STRATEGIC GOALS:'):
                current_section = 'goals'
                continue
            elif line.upper().startswith('UNSOLVED PUZZLES:'):
                current_section = 'puzzles'
                continue
            elif line.upper().startswith('PROGRESS HYPOTHESES:'):
                current_section = 'hypotheses'
                continue
            elif line.upper().startswith('KEY OBSERVATIONS:'):
                current_section = 'observations'
                continue
            elif line.upper().startswith('RECOMMENDED ACTIONS:'):
                current_section = 'actions'
                continue
            elif line.upper().startswith('NEW_KNOWLEDGE:') or line.upper().startswith('NEW KNOWLEDGE:'):
                current_section = 'knowledge'
                continue
            
            # Parse content
            if line.startswith('-') or line.startswith('•'):
                content = line[1:].strip()
                
                if current_section == 'goals' and content:
                    strategic_goals.append(content)
                elif current_section == 'puzzles' and content:
                    # Try to parse puzzle entry
                    puzzle_dict = self._parse_puzzle_line(content)
                    if puzzle_dict:
                        unsolved_puzzles.append(puzzle_dict)
                elif current_section == 'hypotheses' and content:
                    progress_hypotheses.append(content)
                elif current_section == 'observations' and content:
                    key_observations.append(content)
                elif current_section == 'actions' and content:
                    recommended_actions.append(content)
                elif current_section == 'knowledge' and content:
                    # Parse knowledge entry
                    knowledge_entry = self._parse_knowledge_line(content)
                    if knowledge_entry:
                        new_knowledge.append(knowledge_entry)
        
        return AnalysisReport(
            turn=turn,
            strategic_goals=strategic_goals,
            unsolved_puzzles=unsolved_puzzles,
            progress_hypotheses=progress_hypotheses,
            key_observations=key_observations,
            recommended_actions=recommended_actions,
            full_report=response,
            new_knowledge=new_knowledge
        )
    
    def _parse_puzzle_line(self, line: str) -> dict[str, str] | None:
        """Parse a line describing an unsolved puzzle."""
        try:
            # Expected format: "Potential Puzzle: [name], Location: [where seen], Reason: [why it matters and what can be done to solve it]"
            parts = {}

            # Try to extract components
            if 'Potential Puzzle:' in line:
                puzzle_part = line.split('Potential Puzzle:')[1].split(',')[0].strip()
                parts['puzzle'] = puzzle_part

            if 'Location:' in line:
                loc_part = line.split('Location:')[1].split(',')[0].strip()
                parts['location'] = loc_part

            if 'Reason:' in line:
                reason_part = line.split('Reason:')[1].strip()
                parts['description'] = reason_part

            # If we got at least the puzzle name, return it
            if 'puzzle' in parts:
                return {
                    'puzzle': parts.get('puzzle', 'Unknown'),
                    'location': parts.get('location', 'Unknown'),
                    'description': parts.get('description', 'Not analyzed')
                }

            # Fallback: treat entire line as puzzle description
            return {
                'puzzle': line[:50],  # First 50 chars
                'location': 'Unknown',
                'description': line
            }

        except Exception:
            # If parsing fails, return None
            return None
    
    def _parse_knowledge_line(self, line: str) -> KnowledgeEntry | None:
        """Parse a line describing new knowledge."""
        try:
            # Expected format: "Action pattern | Context | Outcome | Score gain | Category | Prerequisites"
            parts = [p.strip() for p in line.split('|')]
            
            if len(parts) < 5:
                # Not enough parts, skip
                return None
            
            action_pattern = parts[0]
            context = parts[1]
            outcome = parts[2]
            
            # Parse score gain
            try:
                score_gain = int(parts[3])
            except (ValueError, IndexError):
                score_gain = 0
            
            category = parts[4] if len(parts) > 4 else "progression"
            
            # Parse prerequisites (if present)
            prerequisites = []
            if len(parts) > 5 and parts[5]:
                prerequisites = [p.strip() for p in parts[5].split(',')]
            
            # Extract location from context if possible
            location = None
            if ',' in context:
                location = context.split(',')[0].strip()
            
            return KnowledgeEntry(
                action_pattern=action_pattern,
                context=context,
                outcome=outcome,
                score_gain=score_gain,
                category=category,
                location=location,
                prerequisites=prerequisites,
                times_confirmed=1
            )
            
        except Exception as e:
            self.logger.warning(f"Failed to parse knowledge line: {line} - {e}")
            return None
    
    def _load_knowledge_base(self) -> None:
        """Load knowledge base from disk."""
        if not self.knowledge_file.exists():
            self.logger.info(f"No existing knowledge base found at {self.knowledge_file}")
            return
        
        try:
            with open(self.knowledge_file, 'r') as f:
                data = json.load(f)
                self.knowledge_base = [KnowledgeEntry.from_dict(entry) for entry in data]
            
            self.logger.info(
                f"📚 Loaded {len(self.knowledge_base)} knowledge entries from {self.knowledge_file}",
                extra={"knowledge_count": len(self.knowledge_base)}
            )
        except Exception as e:
            self.logger.error(f"Failed to load knowledge base: {e}")
            self.knowledge_base = []
    
    def _save_knowledge_base(self) -> None:
        """Save knowledge base to disk."""
        try:
            # Ensure directory exists
            self.knowledge_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert to dict format
            data = [entry.to_dict() for entry in self.knowledge_base]
            
            with open(self.knowledge_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            self.logger.info(
                f"💾 Saved {len(self.knowledge_base)} knowledge entries to {self.knowledge_file}",
                extra={"knowledge_count": len(self.knowledge_base)}
            )
        except Exception as e:
            self.logger.error(f"Failed to save knowledge base: {e}")
    
    def _add_knowledge_if_new(self, new_entry: KnowledgeEntry) -> None:
        """Add knowledge entry if it's not already in the base, or update if it exists."""
        for existing in self.knowledge_base:
            if existing.matches(new_entry):
                # Update existing entry
                existing.times_confirmed += 1
                # Update score if new one is higher
                if new_entry.score_gain > existing.score_gain:
                    existing.score_gain = new_entry.score_gain
                # Merge prerequisites
                for prereq in new_entry.prerequisites:
                    if prereq not in existing.prerequisites:
                        existing.prerequisites.append(prereq)
                self.logger.info(f"📖 Updated existing knowledge: {existing.action_pattern} (confirmed {existing.times_confirmed}x)")
                return
        
        # Add new entry
        self.knowledge_base.append(new_entry)
        self.logger.info(f"✨ New knowledge: {new_entry.action_pattern} (+{new_entry.score_gain} points)")
    
    def get_knowledge_summary(self) -> str:
        """Get a formatted summary of the knowledge base."""
        if not self.knowledge_base:
            return "No game knowledge accumulated yet."
        
        lines = []
        lines.append(f"Global Game Knowledge ({len(self.knowledge_base)} entries):")
        lines.append("=" * 60)
        
        # Sort by score gain and times confirmed
        sorted_kb = sorted(self.knowledge_base, key=lambda k: (k.score_gain, k.times_confirmed), reverse=True)
        
        for entry in sorted_kb[:20]:  # Top 20
            lines.append(f"\n• {entry.action_pattern}")
            lines.append(f"  Category: {entry.category}")
            if entry.location:
                lines.append(f"  Location: {entry.location}")
            lines.append(f"  Outcome: {entry.outcome}")
            if entry.score_gain > 0:
                lines.append(f"  Score: +{entry.score_gain} points")
            if entry.prerequisites:
                lines.append(f"  Prerequisites: {', '.join(entry.prerequisites)}")
            lines.append(f"  Confirmed: {entry.times_confirmed}x")
        
        return "\n".join(lines)

