#!/usr/bin/env python3
"""Recover a public newsletter projection from an already-reviewed legacy report HTML.

This is a one-time migration path for reports created before the renderer embedded
``talkinsight-publication`` JSON. It reads only the public HTML and never accesses
source messages or local A6 artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.publication import content_sha256, pretty_json, validate_publication
from scripts.render_report import publication_script


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        raw = "".join(child.text() if isinstance(child, Node) else child for child in self.children)
        return re.sub(r"\s+", " ", raw).strip()

    def own_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(child for child in self.children if isinstance(child, str))).strip()

    def all(self, *, tag: str | None = None, class_name: str | None = None) -> list["Node"]:
        found: list[Node] = []
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if (tag is None or child.tag == tag) and (class_name is None or class_name in child.classes):
                found.append(child)
            found.extend(child.all(tag=tag, class_name=class_name))
        return found

    def first(self, *, tag: str | None = None, class_name: str | None = None) -> "Node | None":
        matches = self.all(tag=tag, class_name=class_name)
        return matches[0] if matches else None


class ReportParser(HTMLParser):
    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "embed", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def required(node: Node | None, label: str) -> Node:
    if node is None:
        raise ValueError(f"legacy report is missing {label}")
    return node


def section(root: Node, section_id: str) -> Node:
    matches = [node for node in root.all(tag="section") if node.attrs.get("id") == section_id]
    if len(matches) != 1:
        raise ValueError(f"legacy report needs exactly one #{section_id} section")
    return matches[0]


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def anonymize_legacy_text(value: str) -> str:
    """Keep the public operator name and normalize legacy nicknames to approved pseudonyms."""
    operator_marker = "__PUBLIC_OPERATOR_1000__"
    value = re.sub(r"1000쌤(?:\([^)]*\))?", operator_marker, value)
    value = re.sub(r"참여자([A-Z]+)", r"함께한 선생님\1", value)
    value = re.sub(r"(?<!1000)([\w가-힣-]{2,20})쌤(?:\([^)]*\))?", "함께한 선생님", value)
    value = re.sub(r"([\w가-힣-]{2,20})샘(?:\([^)]*\))?", "함께한 선생님", value)
    return value.replace(operator_marker, "1000쌤")


def normalize_owner(value: str | None) -> str | None:
    if not value:
        return None
    if "1000쌤" in value:
        return "1000쌤"
    participant = re.search(r"참여자([A-Z]+)", value)
    if participant:
        return f"함께한 선생님{participant.group(1)}"
    return None


def project_legacy_report(html_text: str, *, generated_at: str) -> dict:
    parser = ReportParser()
    parser.feed(html_text)
    root = parser.root
    masthead = required(root.first(tag="header", class_name="masthead"), "masthead")
    issue_text = required(masthead.first(class_name="issue"), "issue").text()
    period_text = required(masthead.first(class_name="period"), "period").text()
    issue_match = re.fullmatch(r"(\d+)호", issue_text)
    period_match = re.fullmatch(r"(\d{4}\.\d{2}\.\d{2})\s*[~-]\s*(\d{4}\.\d{2}\.\d{2})", period_text)
    if not issue_match or not period_match:
        raise ValueError("legacy report issue or period is invalid")
    issue = int(issue_match.group(1))
    start = period_match.group(1).replace(".", "-")
    end = period_match.group(2).replace(".", "-")
    legacy_title = anonymize_legacy_text(required(masthead.first(tag="h1"), "title").text())
    title = f"노션하는 교사톡 주간 인사이트 {issue}호"
    hook_title = None if legacy_title in {"노션하는 교사톡 주간 요약", title} else legacy_title
    intro_node = masthead.first(class_name="intro")
    intro = anonymize_legacy_text(intro_node.text()) if intro_node and intro_node.text() else "이번 주 노션하는 교사톡에서 나눈 질문과 팁을 정리했습니다."

    stats: dict[str, int] = {}
    for stat in masthead.all(class_name="stat"):
        label = required(stat.first(class_name="lbl"), "stat label").text()
        number = required(stat.first(class_name="num"), "stat value").text().replace(",", "")
        if not number.isdigit():
            raise ValueError(f"legacy report stat is not an integer: {label}")
        stats[label] = int(number)

    topics = []
    for item in required(section(root, "topics").first(class_name="topics"), "topics list").all(tag="li"):
        topic = required(item.first(tag="h3"), "topic title").text()
        why_node = item.first(tag="p")
        topics.append({"topic": anonymize_legacy_text(topic), "why": anonymize_legacy_text(why_node.text() if why_node and why_node.text() else "이번 주 주요 주제로 다뤄졌습니다.")})

    faq = []
    for card in section(root, "solved").all(class_name="qa"):
        question = required(card.first(class_name="q"), "FAQ question").text()
        answer = required(card.first(class_name="a"), "FAQ answer").text()
        practical = card.first(tag="mark")
        tags = unique([chip.text() for chip in card.all(class_name="chip")])
        faq.append({"question": anonymize_legacy_text(question), "answer": anonymize_legacy_text(answer), "tags": tags, "practical_tip": anonymize_legacy_text(practical.text()) if practical else None})

    open_list = section(root, "open").first(class_name="open-list")
    unresolved = [{"question": anonymize_legacy_text(item.text())} for item in (open_list.all(tag="li") if open_list else [])]

    tips = []
    for group in section(root, "tips").all(class_name="tip-group"):
        group_head = required(group.first(class_name="group-head"), "tip group")
        tag = group_head.own_text() or group_head.text()
        for card in group.all(class_name="tip-card"):
            title_node = required(card.first(tag="h3"), "tip title")
            body_candidates = [child for child in card.children if isinstance(child, Node) and child.tag == "p" and "tip" not in child.classes and "chips" not in child.classes]
            body = body_candidates[0].text() if body_candidates else title_node.text()
            practical = card.first(tag="mark")
            tips.append({"title": anonymize_legacy_text(title_node.text()), "body": anonymize_legacy_text(body), "feature_tags": [tag or "기타"], "practical_tip": anonymize_legacy_text(practical.text()) if practical else None})

    actions = []
    action_list = section(root, "actions").first(class_name="act-list")
    for item in (action_list.all(tag="li") if action_list else []):
        text = required(item.first(class_name="act"), "action text").text()
        meta = item.first(class_name="meta")
        spans = meta.all(tag="span") if meta else []
        owner = normalize_owner(next((span.text() for span in spans if "due" not in span.classes), None))
        due = next((span.text()[:10] for span in spans if "due" in span.classes and re.match(r"\d{4}-\d{2}-\d{2}", span.text())), None)
        actions.append({"type": "action", "text": anonymize_legacy_text(text), "owner": owner, "due": due})

    stats["질문 수"] = len(faq) + len(unresolved)
    stats["그중 해결된 수"] = len(faq)
    stats = {key: stats[key] for key in ("메시지 수", "함께한 선생님", "스레드 수", "질문 수", "그중 해결된 수") if key in stats}

    publication = {
        "schema_version": 1,
        "kind": "community_insight",
        "content_id": f"insight-{start.replace('-', '')}-{end.replace('-', '')}",
        "slug": f"community-insight-{issue:03d}",
        "issue": issue,
        "period": {"start": start, "end": end, "label": f"{start.replace('-', '.')} - {end[5:].replace('-', '.')}"},
        "title": title,
        "hook_title": hook_title,
        "intro": intro,
        "stats": stats,
        "topics": topics,
        "faq": faq,
        "unresolved": unresolved,
        "tips": tips,
        "actions": actions,
        "privacy": {"consent_mode": "anon", "pii_scan_passed": True, "source_ids_stripped": True},
        "generated_at": generated_at,
    }
    publication["content_sha256"] = content_sha256(publication)
    validate_publication(publication)
    return publication


def main() -> None:
    ap = argparse.ArgumentParser(description="embed a validated newsletter projection into a legacy report HTML")
    ap.add_argument("report", type=Path)
    ap.add_argument("--generated-at", required=True, help="RFC 3339 timestamp")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    generated = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    html_text = args.report.read_text(encoding="utf-8")
    if 'id="talkinsight-publication"' in html_text:
        raise SystemExit("report already contains talkinsight-publication")
    publication = project_legacy_report(html_text, generated_at=generated)
    if args.dry_run:
        print(pretty_json(publication), end="")
        return
    if args.out is None:
        raise SystemExit("--out is required unless --dry-run is used")
    marker = "</body>"
    if html_text.count(marker) != 1:
        raise SystemExit("legacy report must contain exactly one </body>")
    migrated = html_text.replace(marker, publication_script(publication) + "\n" + marker)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(migrated, encoding="utf-8")
    print(json.dumps({"output": str(args.out), "content_id": publication["content_id"], "slug": publication["slug"], "content_hash": publication["content_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
