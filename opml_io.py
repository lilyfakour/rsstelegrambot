"""OPML 2.0 export/import so subscriptions stay portable."""
import xml.etree.ElementTree as ET


def build_opml(feeds: list[tuple[str, str]]) -> bytes:
    """feeds: list of (title, xml_url). Returns OPML document bytes."""
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "Telegram RSS Bot — subscriptions"
    body = ET.SubElement(opml, "body")
    for title, url in feeds:
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=title or url,
            xmlUrl=url,
        )
    return ET.tostring(opml, encoding="UTF-8", xml_declaration=True)


def parse_opml(data: bytes) -> list[tuple[str, str]]:
    """Extract (title, xml_url) pairs from any OPML document."""
    root = ET.fromstring(data)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for element in root.iter():
        url = (
            element.attrib.get("xmlUrl")
            or element.attrib.get("xmlurl")
            or element.attrib.get("XMLURL")
        )
        if not url or not url.startswith("http"):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = element.attrib.get("text") or element.attrib.get("title") or ""
        out.append((title.strip(), url.strip()))
    return out
