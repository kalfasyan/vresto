import unittest
from unittest.mock import patch

from vresto.ui.widgets.product_viewer import ProductViewerWidget


class TestProductViewerWidget(unittest.TestCase):
    def setUp(self):
        # Mock ProductsManager to avoid credential checks during initialization
        with patch("vresto.ui.widgets.product_viewer.ProductsManager"):
            self.viewer = ProductViewerWidget()

    def test_parse_sentinel2_metadata_strict(self):
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <n1:Level-1C_User_Product xmlns:n1="https://psd-14.sentinel2.eo.esa.int/PSD/User_Product_Level-1C.xsd">
            <n1:General_Info>
                <Product_Info>
                    <PRODUCT_URI>S2B_MSIL1C_20200119T110259.SAFE</PRODUCT_URI>
                </Product_Info>
            </n1:General_Info>
            <n1:Quality_Indicators_Info>
                <Cloud_Coverage_Assessment>4.23</Cloud_Coverage_Assessment>
                <Quality_Control_Checks>
                    <Quality_Inspections>
                        <quality_check checkType="FORMAT_CORRECTNESS">PASSED</quality_check>
                        <quality_check checkType="GEOMETRIC_QUALITY">PASSED</quality_check>
                    </Quality_Inspections>
                </Quality_Control_Checks>
            </n1:Quality_Indicators_Info>
        </n1:Level-1C_User_Product>
        """
        metadata = self.viewer._parse_sentinel2_metadata(xml_content)

        # Check structure
        self.assertIn("General_Info", metadata)
        self.assertIn("Quality_Indicators_Info", metadata)

        # Check nested items
        self.assertEqual(metadata["General_Info"]["Product_Info"]["PRODUCT_URI"], "S2B_MSIL1C_20200119T110259.SAFE")
        self.assertEqual(metadata["Quality_Indicators_Info"]["Cloud_Coverage_Assessment"], "4.23")

        # Check list/attribute handling
        inspections = metadata["Quality_Indicators_Info"]["Quality_Control_Checks"]["Quality_Inspections"]
        self.assertIn("quality_check (FORMAT_CORRECTNESS)", inspections)
        self.assertIn("quality_check (GEOMETRIC_QUALITY)", inspections)
        self.assertEqual(inspections["quality_check (FORMAT_CORRECTNESS)"], "PASSED")

    def test_parse_malformed_xml(self):
        metadata = self.viewer._parse_sentinel2_metadata("not xml")
        self.assertEqual(metadata, {})


if __name__ == "__main__":
    unittest.main()
