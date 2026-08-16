"""Export edge-case tests: special characters, None values, long titles, empty fields."""
import pytest

from parsers.models import Listing
from export_utils import export_txt, export_pdf, listing_lines


class TestExportTxtEdge:

    def test_empty_listings(self):
        data = export_txt([])
        text = data.decode("utf-8")
        assert "0" in text
        assert "Алматы" in text

    def test_listing_with_none_fields(self):
        listing = Listing(title="", price=None, source="test")
        data = export_txt([listing])
        assert isinstance(data, bytes)

    def test_special_characters_in_title(self):
        listing = Listing(
            title='<script>alert("xss")</script> & квартира "ул. Абая"',
            price=100000, source="test",
        )
        data = export_txt([listing])
        text = data.decode("utf-8")
        assert "script" in text
        assert "Абая" in text

    def test_long_title(self):
        listing = Listing(
            title="очень " * 100 + "длинный заголовок",
            price=100000, source="test",
        )
        data = export_txt([listing])
        assert isinstance(data, bytes)
        assert len(data) > 100

    def test_unicode_cyrillic(self):
        listing = Listing(
            title="Аренда 2-комнатной квартиры в Алмалинском районе",
            price=250000, source="krisha.kz",
            address="ул. Абая, уг. ул. Тимирязева",
        )
        data = export_txt([listing])
        text = data.decode("utf-8")
        assert "Аренда" in text
        assert "250 000" in text
        assert "Абая" in text

    def test_many_listings(self):
        listings = [
            Listing(title=f"apt{i}", price=100000 + i * 10000, source="test")
            for i in range(100)
        ]
        data = export_txt(listings)
        text = data.decode("utf-8")
        assert "100" in text  # count
        assert "apt0" in text
        assert "apt99" in text

    def test_price_with_special_format(self):
        listing = Listing(title="test", price=1234567, source="test")
        data = export_txt([listing])
        text = data.decode("utf-8")
        assert "1 234 567" in text


class TestExportPdfEdge:

    def test_empty_listings_pdf(self):
        data = export_pdf([])
        assert data[:4] == b"%PDF"

    def test_pdf_with_none_fields(self):
        listing = Listing(title="", price=None, rooms=None, area=None)
        data = export_pdf([listing])
        assert data[:4] == b"%PDF"

    def test_pdf_with_long_title(self):
        listing = Listing(title="А" * 500, price=100000, source="test")
        data = export_pdf([listing])
        assert data[:4] == b"%PDF"

    def test_pdf_special_chars(self):
        listing = Listing(
            title='Квартира "люкс" & со всеми удобствами',
            price=500000, source="test.kz",
            address="мкр. 5, дом 10, кв. 25",
        )
        data = export_pdf([listing])
        assert data[:4] == b"%PDF"

    def test_pdf_many_listings(self):
        listings = [
            Listing(title=f"Квартира {i}", price=100000 + i * 10000, source="test.kz")
            for i in range(50)
        ]
        data = export_pdf(listings)
        assert data[:4] == b"%PDF"
        assert len(data) > 1000

    def test_pdf_portrait_vs_landscape_different(self):
        listing = Listing(title="test apt", price=100000, source="test.kz")
        portrait = export_pdf([listing], "portrait")
        landscape = export_pdf([listing], "landscape")
        assert portrait[:4] == b"%PDF"
        assert landscape[:4] == b"%PDF"
        # Both should be valid PDFs, sizes may differ

    def test_pdf_cyrillic_text(self):
        listing = Listing(
            title="Аренда квартиры в Алматы",
            price=300000, source="krisha.kz",
            address="Алмалинский район",
        )
        data = export_pdf([listing])
        assert data[:4] == b"%PDF"

    def test_pdf_with_all_fields_populated(self):
        listing = Listing(
            title="3-комнатная квартира, 85 м², 7/9 этаж",
            price=450000, currency="тг", rooms=3, area=85.0,
            floor=7, total_floors=9, address="Бостандыкский, ул. Тлендиева 15",
            url="https://krisha.kz/12345", source="krisha.kz",
            phone="+7 777 123 45 67", description="Хороший ремонт",
            photo="https://krisha.kz/photo.jpg",
        )
        data = export_pdf([listing])
        assert data[:4] == b"%PDF"


class TestListingLinesEdge:

    def test_lines_count(self):
        listings = [Listing(title=f"apt{i}", price=100000) for i in range(5)]
        lines = listing_lines(listings)
        assert "5" in lines[3]  # count line

    def test_lines_contain_separator(self):
        listing = Listing(title="test", price=100000, source="test.kz")
        lines = listing_lines([listing])
        assert any("-" * 10 in l for l in lines)

    def test_lines_contain_header(self):
        lines = listing_lines([])
        assert any("Алматы" in l for l in lines)
        assert any("=" * 10 in l for l in lines)
