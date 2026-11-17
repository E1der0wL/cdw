"""Timezone library details and wall-clock recurrence calculations."""

from datetime import datetime, time, timedelta, timezone
import math

import pytz

from cdw.domain.schedules import TriggerKind, TriggerSpec, aware_datetime
from cdw.i18n import tr


class ScheduleClock:
    SEARCH_DAYS = 15
    UTC = timezone.utc

    @staticmethod
    def zone(name):
        try:
            return pytz.timezone(name)
        except (pytz.UnknownTimeZoneError, AttributeError) as exc:
            raise ValueError(tr("schedule.invalid_timezone").format(timezone=name)) from exc

    @classmethod
    def local_datetime(cls, value: str, zone_name: str):
        """Interpret user wall time strictly; reject missing or ambiguous instants."""
        naive = datetime.fromisoformat(value)
        if naive.tzinfo is not None:
            raise ValueError(tr("schedule.invalid_local_datetime"))
        try:
            return cls.zone(zone_name).localize(naive, is_dst=None).astimezone(cls.UTC)
        except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError) as exc:
            raise ValueError(tr("schedule.ambiguous_datetime")) from exc

    @classmethod
    def _wall_candidate(cls, trigger, day):
        naive = datetime.combine(day, time.fromisoformat(trigger.wall_time))
        zone = cls.zone(trigger.timezone)
        try:
            return zone.localize(naive, is_dst=None).astimezone(cls.UTC)
        except pytz.NonExistentTimeError:
            return None
        except pytz.AmbiguousTimeError:
            # One occurrence only on repeated wall times: choose the first UTC instant.
            return min(zone.localize(naive, is_dst=choice).astimezone(cls.UTC) for choice in (False, True))

    @classmethod
    def next_after(cls, trigger: TriggerSpec, after: datetime, *, inclusive=False):
        cls.zone(trigger.timezone)
        after = after.astimezone(cls.UTC)
        if trigger.kind is TriggerKind.ONCE:
            instant = aware_datetime(trigger.at).astimezone(cls.UTC)
            return instant if instant > after or inclusive and instant == after else None
        if trigger.kind is TriggerKind.INTERVAL:
            anchor = aware_datetime(trigger.anchor_at).astimezone(cls.UTC)
            if after < anchor or inclusive and after == anchor:
                return anchor
            count = (after - anchor).total_seconds() / trigger.interval_seconds
            step = math.ceil(count) if inclusive else math.floor(count) + 1
            return anchor + timedelta(seconds=step * trigger.interval_seconds)
        local_day = after.astimezone(cls.zone(trigger.timezone)).date()
        for offset in range(cls.SEARCH_DAYS):
            day = local_day + timedelta(days=offset)
            if day.weekday() not in trigger.weekdays:
                continue
            candidate = cls._wall_candidate(trigger, day)
            if candidate is not None and (candidate > after or inclusive and candidate == after):
                return candidate
        raise ValueError(tr("schedule.no_next_time"))

    @classmethod
    def latest_due(cls, trigger: TriggerSpec, first_due: datetime, now: datetime):
        """Find the latest missed occurrence without iterating all missed intervals."""
        first_due = first_due.astimezone(cls.UTC)
        now = now.astimezone(cls.UTC)
        if now < first_due:
            return None
        if trigger.kind is TriggerKind.ONCE:
            return first_due
        if trigger.kind is TriggerKind.INTERVAL:
            steps = math.floor((now - first_due).total_seconds() / trigger.interval_seconds)
            return first_due + timedelta(seconds=steps * trigger.interval_seconds)
        local_day = now.astimezone(cls.zone(trigger.timezone)).date()
        for offset in range(cls.SEARCH_DAYS):
            day = local_day - timedelta(days=offset)
            if day.weekday() not in trigger.weekdays:
                continue
            candidate = cls._wall_candidate(trigger, day)
            if candidate is not None and first_due <= candidate <= now:
                return candidate
        return first_due
