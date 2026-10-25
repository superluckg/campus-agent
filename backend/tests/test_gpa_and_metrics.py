import unittest
from decimal import Decimal

from app.tools.gpa_calculator import calculate_gpa


class GPAAndMetricTests(unittest.TestCase):
    def test_gpa_excludes_non_major_courses(self):
        result = calculate_gpa(
            [
                {"课程名称": "高等数学", "学分": 4, "成绩": 90, "课程类别": "专业必修", "修读状态": "正常"},
                {"课程名称": "体育", "学分": 1, "成绩": 100, "课程类别": "体育", "修读状态": "正常"},
                {"课程名称": "数据库", "学分": 2, "成绩": 80, "课程类别": "主修课程", "修读状态": "正常"},
            ]
        )
        self.assertEqual(result.included_courses, 2)
        self.assertEqual(result.excluded_courses, 1)
        self.assertEqual(result.gpa, Decimal("3.67"))


if __name__ == "__main__":
    unittest.main()
