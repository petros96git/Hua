"""
ΣΚΟΠΟΣ
φαιρεί τόνους/διακριτικά και κάνει enforce lowercase στο training , ώστε τα μοντέλα να είναι «insensitive» σε διακριτικά.
"""
from __future__ import annotations
from typing import Any, Dict, List

from rasa.engine.graph import GraphComponent, ExecutionContext
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.storage import ModelStorage
from rasa.engine.storage.resource import Resource
from rasa.shared.nlu.constants import TEXT
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData

# Αντιστοιχίσεις χαρακτήρων με διακριτικά (τονους κτλπ) σε χαρακτήρες χωρίς
REPLACEMENTS = {
    "ά": "α","ί": "ι","ϊ": "ι","ΐ": "ι","ώ": "ω","ΰ": "υ","ϋ": "υ","ύ": "υ","έ": "ε","ό": "ο","ή": "η",
    "Ά": "α","Ί": "ι","Ϊ": "ι","Ώ": "ω","Ϋ": "υ","Ύ": "υ","Έ": "ε","Ό": "ο","Ή": "η",
}

def _normalize(text: str) -> str:
    """Εφαρμόζει αντικαταστάσεις και κάνει lowercase."""
    for k, v in REPLACEMENTS.items():
        text = text.replace(k, v)
    return text.lower()

@DefaultV1Recipe.register(
    [DefaultV1Recipe.ComponentType.MESSAGE_FEATURIZER], is_trainable=False
)
class PreprocessGreekComponent(GraphComponent):
    """normalizer ελληνικών για train."""

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        # Δεν χρειάζεται config
        return {}

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @staticmethod
    def create(
        config: Dict[str, Any],
        model_storage: ModelStorage,
        resource: Resource,
        execution_context: ExecutionContext,
    ) -> "PreprocessGreekComponent":
        return PreprocessGreekComponent(config)

    #αλλαγή εισερχόμενων μηνυμάτων
    def process(self, messages: List[Message]) -> List[Message]:
        for m in messages:
            t = m.get(TEXT)
            if t:
                # add_to_output=False: αντικαθιστά το TEXT επί τόπου
                m.set(TEXT, _normalize(t), add_to_output=False)
        return messages

    # το ίδιο για τα training
    def process_training_data(self, training_data: TrainingData) -> TrainingData:
        for ex in training_data.training_examples:
            t = ex.get(TEXT)
            if t:
                ex.set(TEXT, _normalize(t), add_to_output=False)
        return training_data
