from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Optional

class VoteType(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0
    NULL = 999 

class SourceType(Enum):
    OMNI = auto()      # The General Transformer
    SPECIALIST = auto() # Expert Models (Trend, Chop, Survival, Breakout)
    GUARDRAIL = auto()  # Hardcoded Risk Logic (Stop Loss, etc)

@dataclass
class Signal:
    source: SourceType
    name: str # e.g. "Omni", "Expert_Trend"
    vote: VoteType
    confidence: float # 0.0 to 1.0 (Abs strength of conviction)
    weight: float = 1.0 # Static weight from config
    meta: Dict = field(default_factory=dict) # Debug info (raw logits, regime prob)

@dataclass
class AggregateVote:
    final_vote: VoteType
    net_score: float # Sum of (Vote * Confidence * Weight)
    buy_pressure: float
    sell_pressure: float
    hold_pressure: float
    signals: List[Signal]
