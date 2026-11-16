"""Select entities."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import NarwalConfigEntry, NarwalCoordinator
from .entity import NarwalEntity
from .protocol import AmbientLight, CleanMode, CornerCleaning, Language, MapSummary, MopHumidity, Setting
from .protocol.config import SETTINGS, RobotConfig, edit_message, message_fields

PARALLEL_UPDATES = 1

MOP_OPTIONS: dict[MopHumidity, str] = {
    MopHumidity.DRY: "dry",
    MopHumidity.NORMAL: "normal",
    MopHumidity.WET: "wet",
}
MOP_LEVELS = {name: level for level, name in MOP_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NarwalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        NarwalMopHumiditySelect(coordinator),
        NarwalCleanModeSelect(coordinator),
    ]
    if (config := coordinator.data.config) is not None:
        features = coordinator.static.features
        entities.extend(
            NarwalSettingSelect(coordinator, d)
            for d in SETTING_SELECTS
            if (d.feature is None or features.supports(d.feature)) and config.has(d.setting)
        )
    if coordinator.data.config is not None:
        entities.extend(
            NarwalAppSelect(coordinator, d)
            for d in APP_SELECTS
            if d.feature is None or coordinator.static.features.supports(d.feature)
        )
    if coordinator.static.features.other.get("maxAmbientLightCtrlType", 0) > 0:
        entities.append(NarwalAmbientLightSelect(coordinator))
    if coordinator.data.maps is not None:
        entities.append(NarwalMapSelect(coordinator))
    if coordinator.static.languages:
        entities.append(NarwalLanguageSelect(coordinator))
    async_add_entities(entities)


class NarwalMopHumiditySelect(NarwalEntity, SelectEntity):
    _attr_translation_key = "mop_humidity"
    _attr_options = list(MOP_OPTIONS.values())

    def __init__(self, coordinator: NarwalCoordinator) -> None:
        super().__init__(coordinator, "mop_humidity")

    @property
    def current_option(self) -> str | None:
        return MOP_OPTIONS.get(self.coordinator.data.status.mop_humidity)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_command(
            self.coordinator.robot.set_mop_humidity(MOP_LEVELS[option])
        )
        await self.coordinator.async_request_refresh()


CLEAN_MODE_OPTIONS: dict[CleanMode, str] = {
    CleanMode.SWEEP_MOP: "vacuum_and_mop",
    CleanMode.SWEEP: "vacuum",
    CleanMode.MOP: "mop",
    CleanMode.SWEEP_THEN_MOP: "vacuum_then_mop",
}
CLEAN_MODES = {name: mode for mode, name in CLEAN_MODE_OPTIONS.items()}


class NarwalCleanModeSelect(NarwalEntity, SelectEntity, RestoreEntity):
    """How cleans started from Home Assistant run. The robot has no current
    mode of its own (the app picks one per clean), so it is kept here and
    restored after restarts."""

    _attr_translation_key = "clean_mode"
    _attr_options = list(CLEAN_MODE_OPTIONS.values())

    def __init__(self, coordinator: NarwalCoordinator) -> None:
        super().__init__(coordinator, "clean_mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in CLEAN_MODES:
            self.coordinator.clean_mode = CLEAN_MODES[last.state]

    @property
    def current_option(self) -> str:
        return CLEAN_MODE_OPTIONS[self.coordinator.clean_mode]

    async def async_select_option(self, option: str) -> None:
        self.coordinator.clean_mode = CLEAN_MODES[option]
        self.async_write_ha_state()


@dataclass(frozen=True, kw_only=True)
class NarwalSettingSelectDescription(SelectEntityDescription):
    setting: Setting
    feature: str | None = None  # feature flag the robot must report
    # Option -> feature flag it needs, for options only some robots have.
    option_features: dict[str, str] | None = None


SETTING_SELECTS: tuple[NarwalSettingSelectDescription, ...] = (
    NarwalSettingSelectDescription(
        key="carpet_strategy", setting=Setting.CARPET_STRATEGY, feature="supportCarpetCleanSetting"
    ),
    NarwalSettingSelectDescription(key="mop_drying", setting=Setting.MOP_DRYING),
    NarwalSettingSelectDescription(key="mop_wash_frequency", setting=Setting.MOP_WASH_FREQUENCY),
    NarwalSettingSelectDescription(
        key="corner_cleaning",
        setting=Setting.CORNER_CLEANING,
        feature="supportCornerClean",
        option_features={CornerCleaning.FLEXIBLE_MOP.name.lower(): "supportFlexibleMop"},
    ),
    NarwalSettingSelectDescription(
        key="obstacle_avoidance", setting=Setting.OBSTACLE_AVOIDANCE, feature="supportAvoidModeConfig"
    ),
)


class NarwalSettingSelect(NarwalEntity, SelectEntity):
    """A robot setting with several options; the option keys are the enum's
    member names in lowercase."""

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: NarwalSettingSelectDescription

    def __init__(
        self, coordinator: NarwalCoordinator, description: NarwalSettingSelectDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key
        self._enum = SETTINGS[description.setting].enum
        features = coordinator.static.features
        needs = description.option_features or {}
        options = [member.name.lower() for member in self._enum if member.value]
        self._attr_options = [
            option for option in options if option not in needs or features.supports(needs[option])
        ]

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.config is not None

    @property
    def current_option(self) -> str | None:
        if (config := self.coordinator.data.config) is None:
            return None
        value = config.options.get(self.entity_description.setting)
        name = getattr(value, "name", None)
        return name.lower() if name and name.lower() in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_config(
            self.entity_description.setting, self._enum[option.upper()]
        )


class NarwalAmbientLightSelect(NarwalEntity, SelectEntity):
    """The station's ambient light: off or one of the modes it supports
    (the feature list's maxAmbientLightCtrlType)."""

    _attr_translation_key = "ambient_light"

    def __init__(self, coordinator: NarwalCoordinator) -> None:
        super().__init__(coordinator, "ambient_light")
        top = coordinator.static.features.other.get("maxAmbientLightCtrlType", 0)
        self._attr_options = [m.name.lower() for m in AmbientLight if m.value <= top]

    @property
    def current_option(self) -> str | None:
        mode = AmbientLight.from_value(self.coordinator.data.status.ambient_light)
        name = getattr(mode, "name", None)
        return name.lower() if name and name.lower() in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_command(
            self.coordinator.robot.set_ambient_light(AmbientLight[option.upper()])
        )
        await self.coordinator.async_request_refresh()


def map_label(summary: MapSummary) -> str:
    """How a map is offered: its name from the app, or its number."""
    return summary.name or f"Map {summary.id}"


class NarwalMapSelect(NarwalEntity, SelectEntity):
    """The current map, for robots that keep several (one per floor).
    Choosing another one switches the robot to it."""

    _attr_translation_key = "map"

    def __init__(self, coordinator: NarwalCoordinator) -> None:
        super().__init__(coordinator, "map")

    def _labels(self) -> dict[str, int]:
        labels: dict[str, int] = {}
        for summary in self.coordinator.data.maps or ():
            label = map_label(summary)
            if label in labels:  # two maps with the same name
                label = f"{label} ({summary.id})"
            labels[label] = summary.id
        return labels

    @property
    def options(self) -> list[str]:
        return list(self._labels())

    @property
    def current_option(self) -> str | None:
        current = self.coordinator.data.status.map_id
        return next((label for label, map_id in self._labels().items() if map_id == current), None)

    async def async_select_option(self, option: str) -> None:
        map_id = self._labels()[option]
        if map_id != self.coordinator.data.status.map_id:  # the robot rejects its current map
            await self.coordinator.async_switch_map(map_id)


class NarwalLanguageSelect(NarwalEntity, SelectEntity):
    """The robot's voice language, from the languages it has."""

    _attr_translation_key = "voice_language"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NarwalCoordinator) -> None:
        super().__init__(coordinator, "voice_language")
        self._attr_options = [
            lang.name.lower() for lang in coordinator.static.languages if isinstance(lang, Language)
        ]

    @property
    def current_option(self) -> str | None:
        name = getattr(self.coordinator.data.language, "name", None)
        return name.lower() if name and name.lower() in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_language(Language[option.upper()])


# --- settings the app shows as one choice over several fields -----------------
#
# Each reads the robot's Config and writes it back through /config/set. The
# sub-message settings (rewash, Freo Mind) are read, changed in one field and
# sent back whole, so the room order and the other strategies stay as they are.


def _auto_empty(config: RobotConfig) -> str:
    """Base Station Settings > Auto-empty Frequency (app: Smart / Frequent / Close)."""
    if not config.flags.get(Setting.DUST_GATHERING):
        return "off"
    return "smart" if config.flags.get(Setting.SMART_DUST_GATHERING) else "frequent"


async def _set_auto_empty(coordinator: NarwalCoordinator, option: str) -> None:
    # The app writes the two flags the same way (dustGathering, then smart).
    if option == "off":
        await coordinator.async_set_config(Setting.DUST_GATHERING, False)
        return
    if not coordinator.data.config.flags.get(Setting.DUST_GATHERING):
        await coordinator.async_set_config(Setting.DUST_GATHERING, True)
    await coordinator.async_set_config(Setting.SMART_DUST_GATHERING, option == "smart")


def _auto_empty_level(config: RobotConfig) -> str | None:
    """Dock > Auto-Empty Level (app: Quiet / Strong) = quietDustGathering."""
    if not config.has(Setting.QUIET_DUST_GATHERING):
        return None
    return "quiet" if config.flags.get(Setting.QUIET_DUST_GATHERING) else "strong"


async def _set_auto_empty_level(coordinator: NarwalCoordinator, option: str) -> None:
    await coordinator.async_set_config(Setting.QUIET_DUST_GATHERING, option == "quiet")


STATION_MODES = {
    "vacuum_and_mop": CleanMode.SWEEP_MOP,
    "vacuum_then_mop": CleanMode.SWEEP_THEN_MOP,
    "vacuum": CleanMode.SWEEP,
    "mop": CleanMode.MOP,
}


def _station_mode(config: RobotConfig) -> str | None:
    """Base Station Settings > Default Cleaning Mode for Dock (the dock's
    Start button). Not set yet reads as the app's first option."""
    value = config.options.get(Setting.STATION_CLEAN_MODE)
    if value is None or int(value) == 0:
        return "vacuum_and_mop"
    return next((k for k, v in STATION_MODES.items() if int(v) == int(value)), None)


async def _set_station_mode(coordinator: NarwalCoordinator, option: str) -> None:
    await coordinator.async_set_config(Setting.STATION_CLEAN_MODE, STATION_MODES[option])


# CustomCleanStrategy {1 strategy (1 area, 2 room, 3 smart), 2 areaOption, 3/4 room order}
# Floor Cleaning > Rewash by Area: 15 / 20 / 30 m² or Smart Zone-Based Rewash.
# areaOption is a CleanMopFreq: the app maps DEEP 3 / NORMAL 2 / EFFICIENT 1 to its
# smallest / middle / largest area (8/10/12 m² on other models, 15/20/30 m² on Freo 20).
REWASH = {"area_15": (1, 3), "area_20": (1, 2), "area_30": (1, 1), "smart": (3, None), "by_room": (2, None)}


def _rewash(config: RobotConfig) -> str | None:
    v = message_fields(config.value(Setting.REWASH_STRATEGY))
    strategy, area = v.get(1, 0), v.get(2, 0)
    if strategy == 1:
        return next((k for k, (s, a) in REWASH.items() if s == 1 and a == area), None)
    return next((k for k, (s, _) in REWASH.items() if s == strategy), None)


async def _set_rewash(coordinator: NarwalCoordinator, option: str) -> None:
    raw = coordinator.data.config.value(Setting.REWASH_STRATEGY)
    strategy, area = REWASH[option]
    changes: dict = {1: strategy}
    if area is not None:
        changes[2] = area
    await coordinator.async_set_config(Setting.REWASH_STRATEGY, edit_message(raw, changes))


# SmartCleanParam: 2 overlapLevelStrategy, 4 fanLevelStrategy, 5 mopHumidityStrategy,
# each {1 isCustomizeMode, 2 level}. Not customised = Smart (Freo Mind decides).
# App 2.11.90 NRPitaDeviceSmartCleanController._initBottomSheetMap (0x1ab6680)
# offers Smart and four fan levels here. The shared FanLevel.SUPER=5 is not
# offered in these Freo Mind preferences. These are not manual CleanParam defaults.
FREO_MIND = {
    "smart_fan": (4, {"quiet": 1, "standard": 2, "strong": 3, "super_powerful": 4}),
    "smart_humidity": (5, {"dry": 1, "normal": 2, "wet": 3}),
    "smart_coverage": (2, {"standard": 1, "meticulous": 2}),
}


def _freo_mind(key: str):
    tag, levels = FREO_MIND[key]

    def read(config: RobotConfig) -> str | None:
        sub = message_fields(config.value(Setting.SMART_CLEAN_PARAM)).get(tag)
        v = message_fields(sub if isinstance(sub, bytes) else b"")
        if not v.get(1):
            return "smart"
        return next((k for k, n in levels.items() if n == v.get(2)), None)

    async def write(coordinator: NarwalCoordinator, option: str) -> None:
        raw = coordinator.data.config.value(Setting.SMART_CLEAN_PARAM)
        sub = message_fields(raw).get(tag)
        sub = sub if isinstance(sub, bytes) else b""
        new = edit_message(sub, {1: None}) if option == "smart" else edit_message(sub, {1: 1, 2: levels[option]})
        await coordinator.async_set_config(Setting.SMART_CLEAN_PARAM, edit_message(raw, {tag: new}))

    return ["smart", *levels], read, write


@dataclass(frozen=True, kw_only=True)
class NarwalAppSelectDescription(SelectEntityDescription):
    read_fn: object
    write_fn: object
    feature: str | None = None
    needs: Setting | None = None  # only when the robot reports this sub-message


APP_SELECTS: tuple[NarwalAppSelectDescription, ...] = (
    NarwalAppSelectDescription(
        key="auto_empty_frequency", options=["smart", "frequent", "off"],
        read_fn=_auto_empty, write_fn=_set_auto_empty,
    ),
    NarwalAppSelectDescription(
        key="auto_empty_level", options=["quiet", "strong"], feature="supportQuietDustGathering",
        read_fn=_auto_empty_level, write_fn=_set_auto_empty_level, needs=Setting.QUIET_DUST_GATHERING,
    ),
    NarwalAppSelectDescription(
        key="station_clean_mode", options=list(STATION_MODES), feature="supportStationCleanMode",
        read_fn=_station_mode, write_fn=_set_station_mode,
    ),
    NarwalAppSelectDescription(
        key="rewash", options=["area_15", "area_20", "area_30", "smart"],
        read_fn=_rewash, write_fn=_set_rewash, needs=Setting.REWASH_STRATEGY,
    ),
    *(
        NarwalAppSelectDescription(
            key=key, options=opts, read_fn=read, write_fn=write,
            feature="supportCustomizeSmartCleanParam", needs=Setting.SMART_CLEAN_PARAM,
        )
        for key, (opts, read, write) in ((k, _freo_mind(k)) for k in FREO_MIND)
    ),
)


class NarwalAppSelect(NarwalEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    entity_description: NarwalAppSelectDescription

    def __init__(self, coordinator: NarwalCoordinator, description: NarwalAppSelectDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def available(self) -> bool:
        config = self.coordinator.data.config
        needs = self.entity_description.needs
        return super().available and config is not None and (needs is None or config.has(needs))

    @property
    def options(self) -> list[str]:
        # "by_room" exists in the protocol but not on the Freo 20 screen; show it
        # only if the robot is set to it, so the state is never unknown.
        opts = list(self.entity_description.options)
        if self.entity_description.key == "rewash" and self.current_option == "by_room":
            opts.append("by_room")
        return opts

    @property
    def current_option(self) -> str | None:
        if (config := self.coordinator.data.config) is None:
            return None
        return self.entity_description.read_fn(config)

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.write_fn(self.coordinator, option)
