"""facts.product.model_name nullable

Milestone 3b: some catalogue products are listed by category/variant with **no
printed model number** (e.g. Filling Panels, Fill Containment Cabinets, CBRN
masks, Under Water Communication). ``model_name`` is "as printed" — and when the
catalogue names no model it is legitimately null; inventing a name would violate
the never-guess rule. This relaxes ``facts.product.model_name`` to nullable so
those human-approved rows can be promoted.

**Design note (folds into LLD-DB / data-model §2.3 via the clarification path,
not /prd-change — the field is already "as printed, exact casing"; this only
records that it may be null when no model is printed).** The runtime/display
layer must present such a product by its family name + variant/description and
never show a blank or invent a name (a tool-milestone concern).

Revision ID: 0003_product_model_name_nullable
Revises: 0002_extract_staging
Create Date: 2026-08-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_product_model_name_nullable"
down_revision: Union[str, None] = "0002_extract_staging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("product", "model_name", existing_type=sa.Text(),
                    nullable=True, schema="facts")


def downgrade() -> None:
    op.alter_column("product", "model_name", existing_type=sa.Text(),
                    nullable=False, schema="facts")
