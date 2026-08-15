"""
Passage bank.

Curated rather than generated on purpose: v1 needs to know exactly which phonics
skill each passage exercises, which generated text can't guarantee. Passages are
ordered to match the UFLI sequence in skills.py.

Each carries a literal comprehension question whose answer is a word from the
sentence. Literal recall is what comprehension teaching looks like at this age -
"who sat on the mat?" - and keeping the answer inside the text is also what
makes it checkable without a language model judging free-form speech.
"""

from typing import Dict, List, Optional, TypedDict


class Passage(TypedDict):
    id: str
    text: str
    skills: List[str]
    question: str
    answer: str


PASSAGES: List[Passage] = [
    {
        "id": "p001",
        "text": "Sam has a big fish.",
        "skills": ["cvc_short_a", "cvc_short_i"],
        "question": "What does Sam have?",
        "answer": "fish",
    },
    {
        "id": "p002",
        "text": "The cat sat on a mat.",
        "skills": ["cvc_short_a"],
        "question": "What did the cat sit on?",
        "answer": "mat",
    },
    {
        "id": "p003",
        "text": "A dog can dig in the mud.",
        "skills": ["cvc_short_o", "cvc_short_i"],
        "question": "What can the dog dig in?",
        "answer": "mud",
    },
    {
        "id": "p004",
        "text": "The ship is on the shelf.",
        "skills": ["digraph_sh"],
        "question": "Where is the ship?",
        "answer": "shelf",
    },
    {
        "id": "p005",
        "text": "She will wash the dish.",
        "skills": ["digraph_sh"],
        "question": "What will she wash?",
        "answer": "dish",
    },
    {
        "id": "p006",
        "text": "Chad had lunch with a chick.",
        "skills": ["digraph_ch"],
        "question": "Who had lunch with Chad?",
        "answer": "chick",
    },
    {
        "id": "p007",
        "text": "This is the path to the bath.",
        "skills": ["digraph_th"],
        "question": "Where does the path go?",
        "answer": "bath",
    },
    {
        "id": "p008",
        "text": "Then they think of the thin cloth.",
        "skills": ["digraph_th", "blends_l"],
        "question": "What do they think of?",
        "answer": "cloth",
    },
    {
        "id": "p009",
        "text": "The black flag flaps in the wind.",
        "skills": ["blends_l"],
        "question": "What colour is the flag?",
        "answer": "black",
    },
    {
        "id": "p010",
        "text": "Stop and step on the small step.",
        "skills": ["blends_s"],
        "question": "What do you step on?",
        "answer": "step",
    },
    {
        "id": "p011",
        "text": "A snail can swim past the stump.",
        "skills": ["blends_s", "vowel_team_ai"],
        "question": "What can the snail swim past?",
        "answer": "stump",
    },
    {
        "id": "p012",
        "text": "Jake ate a grape and a cake.",
        "skills": ["vce_long_a"],
        "question": "Who ate the cake?",
        "answer": "Jake",
    },
    {
        "id": "p013",
        "text": "The rain fell on the train track.",
        "skills": ["vowel_team_ai"],
        "question": "What did the rain fall on?",
        "answer": "track",
    },
]

_BY_ID: Dict[str, Passage] = {p["id"]: p for p in PASSAGES}


def get(passage_id: str) -> Optional[Passage]:
    return _BY_ID.get(passage_id)


def all_passages() -> List[Passage]:
    return PASSAGES
