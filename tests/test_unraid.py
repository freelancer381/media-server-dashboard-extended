"""The Unraid template: valid, pointing at this repository's image, its icon and itself."""
import os
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "https://raw.githubusercontent.com/freelancer381/media-server-dashboard-extended/main/"


class Template(unittest.TestCase):
    def setUp(self):
        self.t = ET.parse(os.path.join(ROOT, "unraid", "media-server-dashboard.xml")).getroot()

    def test_image_and_links(self):
        self.assertEqual(self.t.get("version"), "2")
        self.assertEqual(self.t.findtext("Repository"), "ghcr.io/freelancer381/media-server-dashboard-extended:latest")
        self.assertEqual(self.t.findtext("WebUI"), "http://[IP]:[PORT:8900]/")
        for tag in ("TemplateURL", "Icon"):  # files that exist in the repository
            url = self.t.findtext(tag)
            self.assertTrue(url.startswith(RAW), url)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, url[len(RAW):])), url)

    def test_settings(self):
        c = {x.get("Target"): x for x in self.t.findall("Config")}
        self.assertEqual((c["8900"].get("Type"), c["8900"].text), ("Port", "8900"))
        self.assertEqual((c["/data"].get("Type"), c["/data"].get("Mode"), c["/data"].text), ("Path", "rw", "/mnt/user/appdata/media-server-dashboard"))
        self.assertEqual(c["/mnt/user"].get("Mode"), "ro")  # the shares only to read their free space
        self.assertEqual((c["PUID"].text, c["PGID"].text), ("99", "100"))  # appdata's owner on Unraid
        for x in c.values():
            for attr in ("Name", "Type", "Display", "Required", "Mask"):
                self.assertIsNotNone(x.get(attr), (x.get("Target"), attr))


if __name__ == "__main__":
    unittest.main()
