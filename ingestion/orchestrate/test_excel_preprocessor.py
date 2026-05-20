import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ingestion.orchestrate.excel_preprocessor import ExcelPreprocessor


class TestExcelPreprocessor(unittest.TestCase):
    def test_preprocess_retries_on_permission_error_and_succeeds(self):
        preprocessor = ExcelPreprocessor()
        workbook = MagicMock()
        workbook.sheetnames = []
        workbook.save = MagicMock()
        workbook.close = MagicMock()

        load_side_effects = [
            PermissionError("locked"),
            PermissionError("locked"),
            workbook,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source.xlsx"
            target = Path(tmp_dir) / "out" / "target.xlsx"
            source.write_text("dummy content")

            with patch("ingestion.orchestrate.excel_preprocessor.openpyxl.load_workbook", side_effect=load_side_effects) as load_mock, \
                    patch("ingestion.orchestrate.excel_preprocessor.sleep") as sleep_mock:
                result = preprocessor.preprocess(source, target)

        self.assertTrue(result)
        self.assertEqual(load_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
