"""Versioned source-owned catalog identity types.

Names, durations and matching artwork are NEVER canonical identity proof.
These models are additive: existing localStorage and playback use source:id.
"""
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID
import re

SCHEMA_VERSION = 1
PROVIDERS = frozenset(('soundcloud', 'bandcamp', 'audius'))
_ISRC = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')


def mbid(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('Invalid MusicBrainz identifier')
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError('Invalid MusicBrainz identifier') from exc
    if str(parsed) != value.lower():
        raise ValueError('MusicBrainz identifier must be a canonical UUID')
    return str(parsed)


def isrc(value):
    if not value:
        return None
    if not isinstance(value,str):
        raise ValueError('Invalid ISRC')
    clean = value.replace('-', '').upper()
    if not _ISRC.fullmatch(clean):
        raise ValueError('Invalid ISRC')
    return clean


@dataclass(frozen=True)
class Artist:
    name: str
    musicbrainz_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.name.strip() or self.schema_version != SCHEMA_VERSION:
            raise ValueError('Invalid artist')
        object.__setattr__(self,'musicbrainz_id',mbid(self.musicbrainz_id))

    def same_identity(self,other):
        return bool(isinstance(other,Artist) and self.musicbrainz_id
                    and self.musicbrainz_id == other.musicbrainz_id)


@dataclass(frozen=True)
class Release:
    title: str
    artist: Artist
    musicbrainz_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.title.strip() or self.schema_version != SCHEMA_VERSION:
            raise ValueError('Invalid release')
        object.__setattr__(self,'musicbrainz_id',mbid(self.musicbrainz_id))


@dataclass(frozen=True)
class Recording:
    title: str
    artist: Artist
    musicbrainz_id: str | None = None
    isrc: str | None = None
    version: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.title.strip() or self.schema_version != SCHEMA_VERSION:
            raise ValueError('Invalid recording')
        object.__setattr__(self,'musicbrainz_id',mbid(self.musicbrainz_id))
        object.__setattr__(self,'isrc',isrc(self.isrc))
        # An ISRC is useful supporting metadata, not identity proof by itself.

    def same_identity(self,other):
        return bool(isinstance(other,Recording) and self.musicbrainz_id
                    and self.musicbrainz_id == other.musicbrainz_id)


class RecordingAvailability(str,Enum):
    PLAYABLE = 'playable'
    PREVIEW_ONLY = 'preview_only'
    BLOCKED = 'blocked'
    UNKNOWN = 'unknown'
    TEMPORARY_ERROR = 'temporary_error'


@dataclass(frozen=True)
class ProviderRecording:
    source: str
    source_id: str
    recording: Recording | None = None
    availability: RecordingAvailability = RecordingAvailability.UNKNOWN
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if self.source not in PROVIDERS or not isinstance(self.source_id,str) or not self.source_id or self.schema_version != SCHEMA_VERSION:
            raise ValueError('Invalid provider recording')
        if not isinstance(self.availability,RecordingAvailability):
            raise ValueError('Invalid availability')

    @property
    def provider_key(self):
        return self.source + ':' + self.source_id
