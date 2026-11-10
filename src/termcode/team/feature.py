from termcode.config import Config


def fork_teammate_enabled(cfg: Config) -> bool:
    return cfg.features.fork_teammate
