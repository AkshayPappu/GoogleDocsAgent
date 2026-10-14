import unittest

from gdocs_agent.document_map import (
    delete_paragraph_range,
    find_text,
    parse_document,
    parse_document_id,
    plan_insert,
    render_outline,
    resolve_paragraph,
    utf16_len,
)


def paragraph(start, content, style="NORMAL_TEXT", list_id=None):
    end = start + utf16_len(content)
    body = {
        "elements": [
            {"startIndex": start, "endIndex": end, "textRun": {"content": content}}
        ],
        "paragraphStyle": {"namedStyleType": style},
    }
    if list_id:
        body["bullet"] = {"listId": list_id}
    return {"startIndex": start, "endIndex": end, "paragraph": body}


def sample_document():
    return {
        "documentId": "doc123",
        "title": "Plan",
        "lists": {
            "numbered": {
                "listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}
            },
            "bullets": {
                "listProperties": {"nestingLevels": [{"glyphSymbol": "●"}]}
            },
        },
        "body": {
            "content": [
                paragraph(1, "Plan\n", "TITLE"),
                paragraph(6, "Goals\n", "HEADING_1"),
                paragraph(12, "Ship it\n"),
                paragraph(20, "Item\n", list_id="numbered"),
                {
                    "startIndex": 25,
                    "endIndex": 35,
                    "table": {
                        "rows": 1,
                        "columns": 2,
                        "tableRows": [
                            {
                                "tableCells": [
                                    {
                                        "content": [
                                            paragraph(26, "A\n"),
                                        ]
                                    },
                                    {
                                        "content": [
                                            paragraph(29, "B\n"),
                                        ]
                                    },
                                ]
                            }
                        ],
                    },
                },
                paragraph(35, "End\n"),
            ]
        },
    }


class DocumentMapTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_document(sample_document())

    def test_outline_assigns_stable_ids_around_tables(self):
        outline = render_outline(self.doc)
        self.assertIn("p1 [TITLE] Plan", outline)
        self.assertIn("p2 [HEADING_1] Goals", outline)
        self.assertIn("p3 Ship it", outline)
        self.assertIn("p4 [NUMBERED] Item", outline)
        self.assertIn("t1 [TABLE 1x2]", outline)
        self.assertIn("p5 End", outline)
        self.assertEqual([block.id for block in self.doc.blocks], ["p1", "p2", "p3", "p4", "t1", "p5"])

    def test_resolve_heading_and_paragraph_id(self):
        self.assertEqual(resolve_paragraph(self.doc, "goals").id, "p2")
        self.assertEqual(resolve_paragraph(self.doc, "p3").text, "Ship it")

    def test_ambiguous_heading_lists_ids(self):
        raw = sample_document()
        raw["body"]["content"].append(paragraph(39, "Goals\n", "HEADING_2"))
        mapped = parse_document(raw)
        with self.assertRaisesRegex(Exception, "p2"):
            resolve_paragraph(mapped, "Goals")

    def test_insert_positions(self):
        self.assertEqual(plan_insert(self.doc, "Next", "p3", "after"), (20, "Next\n"))
        self.assertEqual(plan_insert(self.doc, "Next", "p5", "after"), (38, "\nNext"))
        self.assertEqual(plan_insert(self.doc, "!", "p3", "append"), (19, "!"))
        self.assertEqual(plan_insert(self.doc, "Next", "document", "end"), (38, "\nNext"))
        self.assertEqual(plan_insert(self.doc, "Intro\n\n", "Goals", "before"), (6, "Intro\n"))

    def test_empty_document_inserts_into_the_blank_paragraph(self):
        empty = parse_document(
            {
                "documentId": "empty",
                "title": "Empty",
                "body": {"content": [paragraph(1, "\n")]},
            }
        )
        self.assertEqual(plan_insert(empty, "Hello", "document", "end"), (1, "Hello"))

    def test_find_text_and_emoji_offsets(self):
        self.assertEqual(find_text(self.doc, "Ship"), [(12, 16)])
        emoji = parse_document(
            {
                "documentId": "emoji",
                "title": "Emoji",
                "body": {
                    "content": [
                        {
                            "startIndex": 10,
                            "endIndex": 15,
                            "paragraph": {
                                "elements": [
                                    {
                                        "startIndex": 10,
                                        "endIndex": 15,
                                        "textRun": {"content": "a😀b\n"},
                                    }
                                ],
                                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                            },
                        }
                    ]
                },
            }
        )
        self.assertEqual(find_text(emoji, "b"), [(13, 14)])

    def test_find_text_across_runs(self):
        mapped = parse_document(
            {
                "documentId": "runs",
                "title": "Runs",
                "body": {
                    "content": [
                        {
                            "startIndex": 1,
                            "endIndex": 13,
                            "paragraph": {
                                "elements": [
                                    {
                                        "startIndex": 1,
                                        "endIndex": 7,
                                        "textRun": {"content": "Hello "},
                                    },
                                    {
                                        "startIndex": 7,
                                        "endIndex": 13,
                                        "textRun": {"content": "world\n"},
                                    },
                                ],
                                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                            },
                        }
                    ]
                },
            }
        )
        self.assertEqual(find_text(mapped, "o w"), [(5, 8)])

    def test_delete_ranges_keep_the_final_newline(self):
        middle = resolve_paragraph(self.doc, "p3")
        last = resolve_paragraph(self.doc, "p5")
        self.assertEqual(delete_paragraph_range(self.doc, middle), (12, 20))
        self.assertEqual(delete_paragraph_range(self.doc, last), (35, 38))

    def test_parse_document_id(self):
        self.assertEqual(
            parse_document_id("https://docs.google.com/document/d/abc_DEF-123/edit?tab=t.0"),
            "abc_DEF-123",
        )
        self.assertEqual(
            parse_document_id("https://drive.google.com/open?id=abc_DEF-1234567890"),
            "abc_DEF-1234567890",
        )


if __name__ == "__main__":
    unittest.main()
