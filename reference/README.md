# System references

Technical documentation for understanding and maintaining the UCSC PPDO archives application. The [project README](../README.md) provides the system overview; end-user instructions are maintained separately.

## Current system documentation

| Document | Covers |
| --- | --- |
| [Configuration](configuration.md) | JSON format, connection settings, archive-path mappings, roles, and operational limits. |
| [API](api.md) | HTTP interfaces, authentication differences, selectors, responses, and integration examples. |
| [Operations](operations.md) | Web/worker responsibilities, indexing, backups, retention, diagnostics, and troubleshooting. |
| [Development](development.md) | Regression-test coverage, verification, repository conventions, and dependency exports. |
| [Development journal](development_journal.md) | Implemented changes, investigations, decisions, verification, and operational follow-up. |

The current reference documents describe the repository's behavior and integration requirements. Actual credentials and infrastructure configuration are maintained outside the version-controlled documentation. Source code and subsequent journal entries take precedence when implementation changes.

## Feature specifications

These documents explain feature design and its original constraints. Features have evolved since some sections were written; a specification's first-release scope is not necessarily the current system's scope.

| Document | Context |
| --- | --- |
| [Project Search](project_search_feature_spec.md) | Metadata matching, canonical project IDs, ranking, filters, and workbook exports. |
| [Project Information](project_info_feature_spec.md) | Project/CAAN/contract relationships, archive counts, and contract presentation. |
| [File Information API](file_info_api_spec.md) | File identity and path selectors, authentication, text, dates, and location response contracts. |
| [Archive Search](user_search_feature_spec.md) | Original file-search design: scopes, canonical results, text coverage, and exports. Later API and telemetry work extends this design. |

## Research and historical context

| Document | Status and purpose |
| --- | --- |
| [Search research memo](user_search_feats_research.md) | Historical investigation and database profiling that informed Archive Search. |
| [Chunked full-text-search plan](archive_search_chunked_fts_plan.md) | Database/ingestion design behind chunked content search; migration ownership is in `business_services_db`. |
| [Filesystem coordination branch notes](fs_coordination_branch_summary.md) | Assessment of a historical coordination branch, including its limitations. It does not establish which capabilities are present on the current branch. |

This directory was previously named `research/`. Historical paths in the development journal are preserved as part of the record; current local documentation links use `reference/`.
