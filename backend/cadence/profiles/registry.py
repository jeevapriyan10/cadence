"""Registry for managing and retrieving railway network profiles."""

from cadence.profiles.base import NetworkProfile


class ProfileRegistry:
    """Registry for discovering and instantiating railway network profiles."""

    _registry: dict[str, type[NetworkProfile]] = {}

    @classmethod
    def register(cls, profile_cls: type[NetworkProfile]) -> None:
        """Register a NetworkProfile class."""
        if not hasattr(profile_cls, "profile_name"):
            raise ValueError(f"Profile class {profile_cls.__name__} must define 'profile_name'.")
        name = profile_cls.profile_name.strip().lower()
        cls._registry[name] = profile_cls

    @classmethod
    def get(cls, profile_name: str) -> NetworkProfile:
        """Retrieve an instantiated NetworkProfile by name."""
        name = profile_name.strip().lower()
        if name not in cls._registry:
            available = ", ".join(sorted(cls._registry.keys()))
            raise KeyError(
                f"Unknown network profile '{profile_name}'. Available profiles: [{available}]"
            )
        return cls._registry[name]()

    @classmethod
    def list_available(cls) -> list[str]:
        """List all available registered profile names in alphabetical order."""
        return sorted(cls._registry.keys())


# Pre-register default profiles on import
from cadence.profiles.local import LocalProfile
from cadence.profiles.mainline import MainlineProfile
from cadence.profiles.metro import MetroProfile

ProfileRegistry.register(MetroProfile)
ProfileRegistry.register(LocalProfile)
ProfileRegistry.register(MainlineProfile)
