from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class BondDataAdapter(ABC):
    """A source of bond panel rows. Swap implementations without touching the model."""

    source: str

    @abstractmethod
    def get_panel(self, start: date, end: date) -> pd.DataFrame:
        """Return month-end rows between start and end conforming to schema.BOND_PANEL."""
