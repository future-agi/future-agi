import io
import zipfile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from sources.models.source_book import SourceBook
from sources.models.scan_page import ScanPage
from sources.services.transkribus_parser import TranskribusParserService
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from accounts.models.user import User
from tfc.middleware.workspace_context import set_workspace_context, clear_workspace_context

SAMPLE_PAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">
    <Page imageFilename="page_0001.jpg" imageWidth="1000" imageHeight="1500">
        <TextRegion id="r1">
            <TextLine id="l1">
                <Coords points="10,20 100,20 100,40 10,40"/>
                <Baseline points="10,35 100,35"/>
                <TextEquiv>
                    <Unicode>Cy commence le premier livre.</Unicode>
                </TextEquiv>
            </TextLine>
            <TextLine id="l2">
                <Coords points="10,50 120,50 120,70 10,70"/>
                <Baseline points="10,65 120,65"/>
                <TextEquiv>
                    <Unicode>En ce temps la estoit un Roy.</Unicode>
                </TextEquiv>
            </TextLine>
        </TextRegion>
    </Page>
</PcGts>
"""


class TranskribusParserUnitTests(TestCase):
    def test_parse_page_xml(self):
        raw_text, metadata = TranskribusParserService.parse_page_xml(SAMPLE_PAGE_XML)
        
        self.assertIn("Cy commence le premier livre.", raw_text)
        self.assertIn("En ce temps la estoit un Roy.", raw_text)
        self.assertEqual(metadata["line_count"], 2)
        
        lines = metadata["lines"]
        self.assertEqual(lines[0]["text"], "Cy commence le premier livre.")
        self.assertEqual(lines[0]["coords"], "10,20 100,20 100,40 10,40")
        self.assertEqual(lines[0]["baseline"], "10,35 100,35")
        
        self.assertEqual(lines[1]["text"], "En ce temps la estoit un Roy.")
        self.assertEqual(lines[1]["coords"], "10,50 120,50 120,70 10,70")
        self.assertEqual(lines[1]["baseline"], "10,65 120,65")

    def test_parse_page_xml_bytes(self):
        bytes_xml = SAMPLE_PAGE_XML.encode("utf-8")
        raw_text, metadata = TranskribusParserService.parse_page_xml(bytes_xml)
        self.assertIn("Cy commence le premier livre.", raw_text)
        self.assertEqual(metadata["line_count"], 2)

    def test_parse_page_xml_namespace_variations(self):
        xml_no_ns = """<PcGts><Page><TextRegion><TextLine id="l1"><TextEquiv><Unicode>Hello world</Unicode></TextEquiv></TextLine></TextRegion></Page></PcGts>"""
        raw_text, metadata = TranskribusParserService.parse_page_xml(xml_no_ns)
        self.assertEqual(raw_text, "Hello world")
        self.assertEqual(metadata["line_count"], 1)

    def test_import_zip_page_number_extraction(self):
        org = Organization.objects.create(name="Org2")
        ws = Workspace.objects.create(name="WS2", organization=org)
        book = SourceBook.objects.create(title="Book 2", organization=org, workspace=ws)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("vol1_page_0005.xml", SAMPLE_PAGE_XML)

        zip_buffer.seek(0)
        pages = TranskribusParserService.import_transkribus_zip(
            book_or_id=book,
            zip_file_path_or_obj=zip_buffer,
            organization=org,
            workspace=ws,
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].page_number, 5)



class TranskribusImportAPITests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(name="Test Workspace", organization=self.org)
        self.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpassword",
            organization=self.org,
        )
        self.client.force_authenticate(user=self.user)
        set_workspace_context(workspace=self.workspace, organization=self.org)

    def tearDown(self):
        clear_workspace_context()
        super().tearDown()

    def test_import_zip(self):
        # Create a book
        book = SourceBook.objects.create(
            title="Amadis de Gaule",
            author="Herberay des Essarts",
            organization=self.org,
            workspace=self.workspace,
        )

        # Create dummy ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("page_0001.xml", SAMPLE_PAGE_XML)
            zip_file.writestr("page_0002.xml", SAMPLE_PAGE_XML.replace("first", "second"))

        zip_buffer.seek(0)
        zip_file_obj = io.BytesIO(zip_buffer.read())
        zip_file_obj.name = "export.zip"

        url = f"/sources/books/{book.id}/import-transkribus/"
        
        # We can pass HTTP_X_WORKSPACE_ID header
        headers = {
            "HTTP_X_WORKSPACE_ID": str(self.workspace.id),
        }
        
        resp = self.client.post(url, {"file": zip_file_obj}, format="multipart", **headers)
        
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["imported_pages_count"], 2)
        
        # Verify ScanPages created in db
        pages = ScanPage.objects.filter(book=book)
        self.assertEqual(pages.count(), 2)
        self.assertEqual(pages[0].page_number, 1)
        self.assertIn("Cy commence le premier livre.", pages[0].raw_ocr_text)
        self.assertEqual(pages[1].page_number, 2)
