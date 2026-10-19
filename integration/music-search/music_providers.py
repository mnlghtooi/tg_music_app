"""Single registry for Music Lab provider capabilities.

The catalog can grow without conflating finding metadata with permission to
play back audio. VK and other providers must not be enabled until audited.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    source: str
    search_tracks: bool = True
    artist_profiles: bool = True
    playback: str = 'in_app'
    notes: str = ''

    def __post_init__(self):
        if not self.source or self.playback not in ('in_app', 'external_only', 'none'):
            raise ValueError('Invalid provider configuration')


# Sources supported by the existing Music Lab local prototype.
PROVIDERS = (
    ProviderSpec('soundcloud'),
    ProviderSpec('bandcamp'),
    ProviderSpec('audius'),
)


def catalog_sources():
    return tuple(p.source for p in PROVIDERS if p.search_tracks)


def artist_sources():
    return tuple(p.source for p in PROVIDERS if p.artist_profiles)


def public_capabilities():
    """Safe metadata only; never include access credentials or stream URLs."""
    return [{'source': p.source, 'search_tracks': p.search_tracks,
             'artist_profiles': p.artist_profiles, 'playback': p.playback}
            for p in PROVIDERS]
