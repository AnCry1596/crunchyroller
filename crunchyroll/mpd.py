import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from .http_client import CrunchyrollHttpClient


def _clean_tag(tag: str) -> str:
    """strip xml namespace prefix"""
    return tag.split("}")[-1] if "}" in tag else tag


def parse_manifest(client: CrunchyrollHttpClient, url: str, debug: bool = False) -> ET.Element:
    """fetch and parse dash mpd manifest"""
    resp = client.do_request("GET", url)
    resp.raise_for_status()

    body = resp.text
    if debug:
        print(f"\n[DEBUG Manifest XML]:\n{body}\n")

    root = ET.fromstring(body)
    return root


def get_pssh(manifest: ET.Element) -> Optional[str]:
    """dig out the cenc pssh string from the manifest"""
    for elem in manifest.iter():
        if _clean_tag(elem.tag) == "AdaptationSet":
            for cp in elem:
                if _clean_tag(cp.tag) == "ContentProtection":
                    # check for pssh
                    for key, val in cp.attrib.items():
                        if "pssh" in key.lower():
                            return val
                    for child in cp:
                        if "pssh" in _clean_tag(child.tag).lower():
                            return child.text
    return None


def get_base_url(
    adaptation_set: ET.Element, is_video_set: bool, quality: str
) -> Tuple[Optional[str], Optional[str]]:
    """find base url and representation id for target quality"""
    reps = [e for e in adaptation_set if _clean_tag(e.tag) == "Representation"]

    for rep in reps:
        rep_id = rep.attrib.get("id", "")
        height = rep.attrib.get("height")
        bandwidth = rep.attrib.get("bandwidth")

        # look for baseurl
        base_url_elem = None
        for child in rep:
            if _clean_tag(child.tag) == "BaseURL":
                base_url_elem = child
                break

        if base_url_elem is None:
            for child in adaptation_set:
                if _clean_tag(child.tag) == "BaseURL":
                    base_url_elem = child
                    break

        base_url = base_url_elem.text if base_url_elem is not None else None

        if is_video_set:
            target_height = quality.replace("p", "")
            if height and str(height) == target_height:
                return base_url, rep_id
        else:
            if "audio/" in rep_id and quality in rep_id:
                return base_url, rep_id
            elif bandwidth is not None:
                bw_val = int(bandwidth)
                num = quality.replace("k", "")
                if num == "192" and bw_val >= 192000:
                    return base_url, rep_id
                elif num == "128" and bw_val >= 128000:
                    return base_url, rep_id
                elif num == "96" and bw_val >= 96000:
                    return base_url, rep_id

    if not reps:
        return None, None

    first_rep = reps[0]
    first_id = first_rep.attrib.get("id", "")
    base_url_elem = None
    for child in first_rep:
        if _clean_tag(child.tag) == "BaseURL":
            base_url_elem = child
            break

    base_url = base_url_elem.text if base_url_elem is not None else None
    print(f"Audio quality {quality} not found, deferring to {first_id}")
    return base_url, first_id


def expand_timeline(
    adaptation_set: ET.Element, start_number: int = 1
) -> List[int]:
    """expand segment timelines into a list of numbers"""
    s_elements = []
    for elem in adaptation_set.iter():
        if _clean_tag(elem.tag) == "S":
            s_elements.append(elem)

    start_num = start_number
    for elem in adaptation_set.iter():
        if _clean_tag(elem.tag) == "SegmentTemplate":
            sn = elem.attrib.get("startNumber")
            if sn:
                try:
                    start_num = int(sn)
                except ValueError:
                    pass
            break

    result = []
    seg_num = start_num

    for s in s_elements:
        repeat = 0
        r_val = s.attrib.get("r")
        if r_val is not None:
            repeat = int(r_val)
            if repeat < 0:
                repeat = 0

        total = repeat + 1
        for _ in range(total):
            result.append(seg_num)
            seg_num += 1

    return result

