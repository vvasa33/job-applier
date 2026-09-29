"""Normalized description of one Workday application page. It contains no chosen answers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationField:
    field_id: str
    label: str
    type: str
    options: tuple[str, ...]
    required: bool
    current_value: str
    confidence: float
    selector: str
    option_selectors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NavigationButton:
    button_id: str
    label: str
    kind: str
    selector: str


@dataclass(frozen=True)
class WorkdayPage:
    url: str
    title: str
    heading: str
    fields: tuple[ApplicationField, ...]
    navigation: tuple[NavigationButton, ...]

    def field(self, field_id: str) -> ApplicationField:
        for item in self.fields:
            if item.field_id == field_id:
                return item
        raise KeyError(field_id)

    def button(self, button_id: str) -> NavigationButton:
        for item in self.navigation:
            if item.button_id == button_id:
                return item
        raise KeyError(button_id)
