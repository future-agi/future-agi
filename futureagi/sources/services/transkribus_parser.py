import re
import zipfile
import xml.etree.ElementTree as ET
import structlog
from django.db import transaction
from sources.models.source_book import SourceBook
from sources.models.scan_page import ScanPage

logger = structlog.get_logger(__name__)


class TranskribusParserService:
    @staticmethod
    def clean_tag(tag):
        """Helper to extract local name ignoring XML namespaces."""
        if isinstance(tag, str) and "}" in tag:
            return tag.split("}", 1)[1]
        return tag or ""

    @classmethod
    def parse_page_xml(cls, xml_content):
        """
        Parses Transkribus PAGE-XML content.
        Extracts reading order text, lines, and baseline/region coordinates.
        Accepts string or bytes.
        """
        if isinstance(xml_content, str):
            xml_content = re.sub(r'^\s*<\?xml[^>]*\?>', '', xml_content)
            root = ET.fromstring(xml_content)
        else:
            root = ET.fromstring(xml_content)
        
        lines_data = []
        full_text_parts = []
        
        for elem in root.iter():
            if cls.clean_tag(elem.tag) == "TextLine":
                line_elem = elem
                text_val = ""
                coords_val = ""
                baseline_val = ""
                line_id = line_elem.get("id", "")

                for child in line_elem:
                    child_tag = cls.clean_tag(child.tag)
                    if child_tag == "TextEquiv":
                        for gchild in child:
                            if cls.clean_tag(gchild.tag) == "Unicode" and gchild.text:
                                text_val = gchild.text.strip()
                    elif child_tag == "Coords":
                        coords_val = child.get("points", "")
                    elif child_tag == "Baseline":
                        baseline_val = child.get("points", "")

                lines_data.append({
                    "text": text_val,
                    "coords": coords_val,
                    "baseline": baseline_val,
                    "id": line_id,
                })
                if text_val:
                    full_text_parts.append(text_val)
                
        raw_ocr_text = "\n".join(full_text_parts)
        ocr_metadata = {
            "lines": lines_data,
            "line_count": len(lines_data)
        }
        
        return raw_ocr_text, ocr_metadata

    @classmethod
    def import_transkribus_zip(cls, book_or_id, zip_file_path_or_obj, organization=None, workspace=None):
        """
        Imports a ZIP package of Transkribus PAGE-XML files.
        Associates the extracted scan pages with the given SourceBook.
        """
        if isinstance(book_or_id, SourceBook):
            book = book_or_id
        else:
            query_kwargs = {"id": book_or_id}
            if organization:
                query_kwargs["organization"] = organization
            if workspace:
                query_kwargs["workspace"] = workspace
            book = SourceBook.objects.get(**query_kwargs)
        
        org = organization or book.organization
        ws = workspace or book.workspace

        created_pages = []
        
        # Open ZIP file
        with zipfile.ZipFile(zip_file_path_or_obj, 'r') as archive:
            # Sort file names to ensure correct sequence/page ordering
            namelist = sorted(archive.namelist())
            
            # Filter for xml files (excluding metadata or directories)
            xml_files = [
                f for f in namelist
                if f.lower().endswith('.xml') and not f.startswith('__MACOSX') and not re.split(r'[/\\]', f)[-1].startswith('.')
            ]
            
            with transaction.atomic():
                for idx, xml_path in enumerate(xml_files, start=1):
                    # Read file content as bytes
                    raw_bytes = archive.read(xml_path)
                    
                    try:
                        raw_ocr_text, ocr_metadata = cls.parse_page_xml(raw_bytes)
                    except Exception as e:
                        logger.warning(
                            "Skipping malformed PAGE-XML file in Transkribus archive",
                            xml_path=xml_path,
                            error=str(e),
                            exc_info=True,
                        )
                        continue
                    
                    # Extract page number from filename if possible (e.g., page_0001.xml -> 1)
                    filename = re.split(r'[/\\]', xml_path)[-1]
                    digits = re.findall(r'\d+', filename)
                    page_number = int(digits[-1]) if digits else idx
                    
                    page_label = f"Page {page_number}"
                    
                    # Deduplicate/update if page already exists, otherwise create
                    page, _ = ScanPage.objects.update_or_create(
                        book=book,
                        page_number=page_number,
                        defaults={
                            "organization": org,
                            "workspace": ws,
                            "page_label": page_label,
                            "raw_ocr_text": raw_ocr_text,
                            "ocr_metadata": ocr_metadata,
                        }
                    )
                    created_pages.append(page)
                    
        return created_pages

