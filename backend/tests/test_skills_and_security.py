import tempfile
import unittest
from pathlib import Path

from app.schemas.enums import AgentName, IntentName
from app.services.upload_resolver import ScopedUploadResolver
from app.skills.manager import SkillManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SkillsAndSecurityTests(unittest.TestCase):
    def test_skill_manager_maps_intent_and_policy(self):
        manager = SkillManager(PROJECT_ROOT / "skills")
        selected = manager.select(
            "请分析我上传的简历并给出总结",
            AgentName.CAREER,
            IntentName.CAREER_RESUME,
            embedding_scores={"resume-analysis": 0.9},
        )
        names = {item.name for item in selected.selected}
        self.assertIn("resume-analysis", names)
        self.assertLessEqual(len(selected.selected), 3)

    def test_scoped_upload_resolver_rejects_unknown_id(self):
        with tempfile.TemporaryDirectory() as directory:
            resolver = ScopedUploadResolver(Path(directory), [])
            with self.assertRaises(ValueError):
                resolver.resolve("not-allowed")


if __name__ == "__main__":
    unittest.main()
