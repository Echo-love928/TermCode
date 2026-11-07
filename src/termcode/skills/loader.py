"""扫描项目和用户的 Skill，并在调用时重读已注册文件。"""

import logging
from pathlib import Path

from termcode.skills.parser import SkillDef, SkillParseError, parse_skill_file

PROJECT_SKILLS_DIR = ".termcode/skills"
USER_SKILLS_DIR = "~/.termcode/skills"
log = logging.getLogger(__name__)


class SkillLoader:
    def __init__(self, work_dir: str | Path, *, user_dir: Path | None = None) -> None:
        self._project_dir = Path(work_dir).absolute() / PROJECT_SKILLS_DIR
        self.user_dir = (user_dir or Path(USER_SKILLS_DIR).expanduser()).absolute()
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}

    def load_all(self) -> None:
        skills: dict[str, SkillDef] = {}
        for root, source in ((self._project_dir, "project"), (self.user_dir, "user")):
            for skill in self._scan_directory(root, source):
                skills.setdefault(skill.name, skill)
        self._skills = skills
        self._cache = skills.copy()

    reload = load_all

    @staticmethod
    def _scan_directory(root: Path, source: str) -> list[SkillDef]:
        try:
            entries = sorted(root.iterdir())
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("Skipping %s skill directory: %s", source, exc)
            return []
        skills = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_directory = entry.is_dir()
                path = entry / "SKILL.md" if is_directory else entry
                if path.suffix == ".md" and path.is_file():
                    skills.append(parse_skill_file(path, is_directory=is_directory))
            except (OSError, SkillParseError) as exc:
                log.warning("Skipping %s skill '%s': %s", source, entry.stem, exc)
        return skills

    def get(self, name: str) -> SkillDef | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        try:
            current = parse_skill_file(
                skill.source_path, is_directory=skill.is_directory
            )
            if current.name != name:
                raise SkillParseError("name 已修改，请执行 /skill reload")
        except SkillParseError as exc:
            log.warning("Skill '%s' 热重载失败，使用缓存: %s", name, exc)
            return self._cache[name]
        self._cache[name] = current
        return current

    def get_catalog(self) -> list[tuple[str, str]]:
        return [(name, self._skills[name].description) for name in sorted(self._skills)]

    def get_source_label(self, name: str) -> str | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        return (
            "project" if skill.source_path.is_relative_to(self._project_dir) else "user"
        )
