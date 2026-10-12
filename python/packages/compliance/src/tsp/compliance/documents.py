"""The manifest of published legal documents: id, version, checksum, whether consent is required."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class Document(BaseModel):
    id: str
    title: str
    version: str
    sha256: str
    url: str
    requires_consent: bool = False


class Manifest(BaseModel):
    documents: dict[str, Document]

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        return cls.model_validate(json.loads(Path(path).read_text()))

    def required(self) -> list[Document]:
        return [d for d in self.documents.values() if d.requires_consent]

    def get(self, doc_id: str) -> Document:
        try:
            return self.documents[doc_id]
        except KeyError:
            raise KeyError(f"unknown legal document: {doc_id}") from None
