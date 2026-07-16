# Normative document audit

Status: **passed**

```json
{
  "all_targeted_negative_fixtures_rejected": true,
  "documents": [
    "CONTRIBUTING.md",
    "README.md",
    "SCORING-MODEL.md",
    "SPEC.md",
    "docs/methodology.md",
    "docs/result-schema.md"
  ],
  "findings": [],
  "formula_documents_valid": true,
  "invocation": "uv run python scripts/normative_document_audit.py --output verification/final-methodology/normative-document-audit.json",
  "negative_fixtures": [
    {
      "document": "SPEC.md",
      "findings": [
        {
          "document": "SPEC.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    },
    {
      "document": "SCORING-MODEL.md",
      "findings": [
        {
          "document": "SCORING-MODEL.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    },
    {
      "document": "README.md",
      "findings": [
        {
          "document": "README.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    },
    {
      "document": "CONTRIBUTING.md",
      "findings": [
        {
          "document": "CONTRIBUTING.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    },
    {
      "document": "docs/methodology.md",
      "findings": [
        {
          "document": "docs/methodology.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    },
    {
      "document": "docs/result-schema.md",
      "findings": [
        {
          "document": "docs/result-schema.md",
          "retired_fields": [
            "Token accounting v2",
            "common_regression_pass_fraction"
          ],
          "stale_formula_fragments": [
            "output_tokens_including_reasoning + reasoning_output_tokens"
          ]
        }
      ],
      "rejected": true
    }
  ],
  "production_formula": {
    "ast": "BinOp(left=BinOp(left=Call(func=Name(id='float', ctx=Load()), args=[Subscript(value=Name(id='usage', ctx=Load()), slice=Constant(value='observed_non_cached_input_tokens'), ctx=Load())]), op=Add(), right=BinOp(left=Name(id='cache_weight', ctx=Load()), op=Mult(), right=Call(func=Name(id='float', ctx=Load()), args=[Subscript(value=Name(id='usage', ctx=Load()), slice=Constant(value='cached_input_tokens'), ctx=Load())]))), op=Add(), right=Call(func=Name(id='float', ctx=Load()), args=[Subscript(value=Name(id='usage', ctx=Load()), slice=Constant(value='output_tokens_including_reasoning'), ctx=Load())]))",
    "attributes": [],
    "fields": [
      "cached_input_tokens",
      "observed_non_cached_input_tokens",
      "output_tokens_including_reasoning"
    ],
    "names": [
      "cache_weight",
      "float",
      "float",
      "float",
      "usage",
      "usage",
      "usage"
    ]
  },
  "production_formula_valid": true,
  "schema_id": "normative-document-audit-current",
  "status": "passed"
}
```
