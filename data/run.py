"""Day 06 - Data ingestion pipeline: so sanh 3 chien luoc A / B / C.

Cai dat:
    pip install chromadb sentence-transformers pypdf

Dau vao (do hoc vien tao, xem GUIDE.md / BENCHMARK.md):
    data/manifest.json        nguon, quyen, trang duoc tinh, truong metadata
                              (chap nhan dang chuan co `sources[]` HOAC dang phang 1 tai lieu)
    data/pages.jsonl          moi dong 1 trang/don vi:
                              {"document_id","source","page","text", <truong loc>...}
    benchmark/questions.json  >=20 cau (>=15 dev / >=5 test, >=3 co filter, >=1 test co filter)

Lenh:
    python run.py build-corpus  # (tuy chon) trich xuat data/pages.jsonl tu PDF, section/chapter lay tu outline PDF
    python run.py validate    # chi kiem tra corpus + benchmark
    python run.py run         # validate -> ingest A/B/C -> dev -> chot chien luoc -> test
                              #   -> trace -> mau cham thu cong        (mac dinh)
    python run.py summarize   # sau khi cham tay: tong hop bang cho bao cao

Cau hinh:
    A: chunk co dinh (500 ky tu, overlap 50), khong filter
    B: chunk theo heading/doan (toi da 800 ky tu), khong filter
    C: giong B, them metadata filter CHI cho cau hoi co filter (B-C chi khac dung 1 yeu to)
Embedding: sentence-transformers (mac dinh intfloat/multilingual-e5-small), cung model cho 3 cau hinh.
Vector store: Chroma persistent, cosine, HNSW (approximate). Backend loi -> bao loi, khong fallback.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

STRATEGIES = ("A", "B", "C")
TOP_K = 3
RESERVED_META = {"document_id", "source", "page", "chunk_index", "char_start", "char_end"}
FORBIDDEN_FILTER_KEYS = {"document_id", "source", "page"}  # loc theo gold = ro ri dap an
MIN_PAGES = 30
MIN_TEXT_UNIT_CHARS = 1500
HYPOTHESES = {
    "A": ("Baseline: chunk co dinh 500/50, khong filter", "-"),
    "B": ("Chunk theo doan/heading giu tron y hon A", "chunking (A->B)"),
    "C": ("Filter metadata tang Hit@k cho cau co filter", "metadata filter (B->C)"),
}

log = logging.getLogger("day06")


class PipelineError(RuntimeError):
    """Loi nghiep vu/backend: dung pipeline, khong am tham fallback."""


# --------------------------------------------------------------------------- config
def find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "BENCHMARK.md").exists():
            return p
    return Path.cwd()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["build-corpus", "validate", "run", "summarize"])
    ap.add_argument("--root", type=Path, default=None, help="thu muc goc repo (mac dinh: tu tim BENCHMARK.md)")
    ap.add_argument("--manifest", type=Path, default=None)
    ap.add_argument("--pages", type=Path, default=None)
    ap.add_argument("--questions", type=Path, default=None)
    ap.add_argument("--pdf", type=Path, default=None, help="PDF nguon cho build-corpus")
    ap.add_argument("--exclude-sections", default="Front Cover,Contents,References",
                    help="build-corpus: cac muc outline bi loai (bia, muc luc, tai lieu tham khao)")
    ap.add_argument("--min-chars", type=int, default=20, help="build-corpus: trang phai co > so ky tu nay")
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--query-prefix", default="query: ", help="e5 can 'query: '; model khac thi dat rong")
    ap.add_argument("--doc-prefix", default="passage: ", help="e5 can 'passage: '; model khac thi dat rong")
    ap.add_argument("--device", default=None)
    ap.add_argument("--fixed-size", type=int, default=500)
    ap.add_argument("--fixed-overlap", type=int, default=50)
    ap.add_argument("--para-max", type=int, default=800)
    ap.add_argument("--para-min", type=int, default=200)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cost-per-1m-tokens", type=float, default=0.0, help="USD; 0 neu chay local")
    ap.add_argument("--force-reingest", action="store_true")
    ap.add_argument("--relock", action="store_true", help="cho phep doi split sau khi da khoa (se bi ghi log)")
    a = ap.parse_args()
    a.root = (a.root or find_root(Path(__file__).resolve().parent)).resolve()
    a.manifest = a.manifest or a.root / "data" / "manifest.json"
    a.pages = a.pages or a.root / "data" / "pages.jsonl"
    a.questions = a.questions or a.root / "benchmark" / "questions.json"
    a.store_dir = a.root / "artifacts" / "chroma_store"
    a.results = a.root / "results"
    a.artifacts = a.root / "artifacts"
    a.collection_prefix = "day06"
    return a


# --------------------------------------------------------------------------- text utils
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s).replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    s = _CTRL.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fold(s: str) -> str:
    """So khop evidence: NFC, gop khoang trang, casefold."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s))).strip().casefold()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def page_sort_key(page: Any) -> tuple:
    try:
        return (0, int(page), "")
    except (TypeError, ValueError):
        return (1, 0, str(page))


# --------------------------------------------------------------------------- corpus
def normalize_manifest(m: dict) -> dict:
    """Dua manifest ve dang chuan. Nhan: (1) dang chuan `sources[]`; (2) dang phang 1 tai lieu
    (document_id, source_title, license, total_pages, counted_pages, page_counting_rules, metadata_fields list)."""
    out = dict(m)
    if "sources" not in m:
        doc = m.get("document_id")
        if not doc:
            raise PipelineError("manifest dang phang can 'document_id'; hoac dung dang chuan co 'sources'")
        out["sources"] = [{
            "source_id": doc, "title": m.get("source_title") or m.get("title") or doc,
            "source_path_or_url": m.get("source_path_or_url") or doc,
            "license_or_permission": m.get("license_or_permission") or m.get("license"),
            "accessed_at": m.get("accessed_at"), "counted_pages": m.get("counted_pages"),
            "physical_pages": m.get("total_pages"), "unit_type": m.get("unit_type", "pdf_page"),
            "publisher": m.get("publisher"), "version": m.get("publication_year"),
            "char_count": m.get("char_count")}]
    out["counting_rule"] = m.get("counting_rule") or m.get("page_counting_rules")
    mf = m.get("metadata_fields") or {}
    if isinstance(mf, list):  # chi liet ke ten truong; document_id/source/page luon co
        mf = {n: {"type": "auto"} for n in mf if n not in FORBIDDEN_FILTER_KEYS}
    out["metadata_fields"] = mf
    return out


def load_corpus(cfg) -> tuple[dict, list[dict], list[str]]:
    """Doc manifest + pages, chuan hoa, kiem tra; tra ve (manifest, pages, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not cfg.manifest.exists():
        raise PipelineError(f"Thieu manifest: {cfg.manifest}")
    if not cfg.pages.exists():
        raise PipelineError(f"Thieu pages: {cfg.pages}")
    manifest = normalize_manifest(json.loads(cfg.manifest.read_text(encoding="utf-8")))
    sources = {s.get("source_id"): s for s in manifest.get("sources", [])}
    need = ("source_id", "title", "source_path_or_url", "license_or_permission", "counted_pages")
    for sid, s in sources.items():
        miss = [k for k in need if s.get(k) is None]
        if miss:
            errors.append(f"manifest source {sid}: thieu {miss}")
        if not s.get("accessed_at"):
            warnings.append(f"manifest source {sid}: thieu 'accessed_at' (ngay truy cap) - hay dien vao")
    meta_fields: dict = manifest.get("metadata_fields", {})
    if not meta_fields:
        errors.append("manifest.metadata_fields rong: can >=1 truong loc ngoai document_id/source/page")
    if not manifest.get("counting_rule"):
        errors.append("manifest thieu quy tac tinh trang ('counting_rule' hoac 'page_counting_rules')")

    type_map = {"str": str, "int": int, "float": (int, float), "bool": bool}
    pages: list[dict] = []
    seen_keys: set = set()
    seen_hash: dict[str, tuple] = {}
    ingest_err_path = cfg.results / "ingest_errors.jsonl"
    cfg.results.mkdir(parents=True, exist_ok=True)
    bad_lines: list[dict] = []
    with cfg.pages.open(encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                doc, page = rec["document_id"], rec["page"]
                text = normalize_text(rec["text"])
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                bad_lines.append({"line": ln, "error": repr(e)})
                continue
            if doc not in sources:
                msg = f"document_id '{doc}' khong co trong manifest (vd dong {ln})"
                if msg not in errors and not any(e.startswith(f"document_id '{doc}'") for e in errors):
                    errors.append(msg)
                continue
            if (doc, str(page)) in seen_keys:
                errors.append(f"dong {ln}: trung (document_id,page) = ({doc},{page})")
                continue
            seen_keys.add((doc, str(page)))
            if not text:
                bad_lines.append({"line": ln, "error": "trang rong sau chuan hoa", "page": page})
                continue
            h = sha256_text(fold(text))
            if h in seen_hash:
                errors.append(f"dong {ln}: noi dung trung voi {seen_hash[h]} (khong nhan ban de du so luong)")
                continue
            seen_hash[h] = (doc, page)
            extra: dict[str, Any] = {}
            for name, spec in meta_fields.items():
                val = rec.get(name)
                if val is None or val == "":
                    continue  # khong doan gia tri khong co trong nguon
                t = spec.get("type", "auto")
                if t == "auto":
                    good = isinstance(val, (str, int, float, bool))
                else:
                    good = isinstance(val, type_map.get(t, str)) and not (t == "int" and isinstance(val, bool))
                if not good:
                    errors.append(f"dong {ln}: truong '{name}'={val!r} sai kieu {t}")
                    continue
                extra[name] = val
            unit = sources[doc].get("unit_type", "pdf_page")
            if unit == "text_unit" and len(text) < MIN_TEXT_UNIT_CHARS:
                errors.append(f"dong {ln}: don vi text ({doc},{page}) {len(text)} < {MIN_TEXT_UNIT_CHARS} ky tu")
            pages.append({"document_id": doc, "source": rec.get("source", sources[doc]["source_path_or_url"]),
                          "page": page, "text": text, "chars": len(text), "extra": extra})
    if bad_lines:
        with ingest_err_path.open("w", encoding="utf-8") as f:
            for b in bad_lines:
                f.write(json.dumps(b, ensure_ascii=False) + "\n")
        warnings.append(f"{len(bad_lines)} dong loi/rong -> {ingest_err_path}")

    # doi chieu manifest <-> pages
    by_doc: dict[str, list[dict]] = {}
    for p in pages:
        by_doc.setdefault(p["document_id"], []).append(p)
    for sid, s in sources.items():
        got = {str(p["page"]) for p in by_doc.get(sid, [])}
        cp = s.get("counted_pages")
        if isinstance(cp, list):
            want = {str(x) for x in cp}
            if got != want:
                errors.append(f"{sid}: counted_pages trong manifest != pages.jsonl "
                              f"(thieu {sorted(want - got)[:5]}, thua {sorted(got - want)[:5]})")
        elif isinstance(cp, int) and cp != len(got):
            warnings.append(f"{sid}: manifest ghi counted_pages={cp} nhung pages.jsonl co {len(got)} trang "
                            f"-> chay `python run.py build-corpus` de dong bo")
        if "char_count" in s and s["char_count"] != sum(p["chars"] for p in by_doc.get(sid, [])):
            warnings.append(f"{sid}: char_count manifest != tong ky tu sau chuan hoa")
    if len(pages) < MIN_PAGES:
        errors.append(f"Chi co {len(pages)} trang noi dung hop le (<{MIN_PAGES})")
    ok_filter = False
    for name in meta_fields:
        if len({p["extra"][name] for p in pages if name in p["extra"]}) >= 2:
            ok_filter = True
    for name in meta_fields:
        if not any(name in p["extra"] for p in pages):
            warnings.append(f"truong metadata '{name}' khai bao trong manifest nhung khong co trang nao co gia tri")
    if not ok_filter:
        errors.append("Khong truong loc nao co >=2 gia tri phan biet trong corpus")
    if errors:
        raise PipelineError("Corpus khong hop le:\n  - " + "\n  - ".join(errors))
    pages.sort(key=lambda p: (str(p["document_id"]), page_sort_key(p["page"])))
    return manifest, pages, warnings


# --------------------------------------------------------------------------- benchmark
def load_questions(cfg, pages: list[dict], manifest: dict) -> list[dict]:
    if not cfg.questions.exists():
        raise PipelineError(f"Thieu benchmark: {cfg.questions}")
    raw = json.loads(cfg.questions.read_text(encoding="utf-8"))
    qs = raw["questions"] if isinstance(raw, dict) else raw
    page_index = {(p["document_id"], str(p["page"])): p for p in pages}
    meta_fields = set(manifest["metadata_fields"])
    errors: list[str] = []
    ids: set = set()
    req = ("id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote")
    for q in qs:
        miss = [k for k in req if not q.get(k) and q.get(k) != 0]
        if miss:
            errors.append(f"{q.get('id', '?')}: thieu {miss}")
            continue
        if q["id"] in ids:
            errors.append(f"{q['id']}: trung id")
        ids.add(q["id"])
        if q["split"] not in ("dev", "test"):
            errors.append(f"{q['id']}: split phai la dev/test")
        targets = [{"source_id": q["gold_source_id"], "page": q["gold_page"], "evidence_quote": q["evidence_quote"]}]
        for alt in q.get("alt_gold", []):
            targets.append({"source_id": alt["source_id"], "page": alt["page"],
                            "evidence_quote": alt.get("evidence_quote", q["evidence_quote"])})
        q["_targets"] = targets
        for t in targets:
            pg = page_index.get((t["source_id"], str(t["page"])))
            if pg is None:
                errors.append(f"{q['id']}: gold ({t['source_id']},{t['page']}) khong co trong corpus")
            elif fold(t["evidence_quote"]) not in fold(pg["text"]):
                errors.append(f"{q['id']}: evidence khong nam trong ({t['source_id']},{t['page']}) sau chuan hoa")
        if fold(q["evidence_quote"]) in fold(q["question"]):
            log.warning("%s: evidence nam nguyen trong cau hoi - nguy co ro ri dap an", q["id"])
        flt = q.get("filter") or {}
        for k, v in flt.items():
            if k in FORBIDDEN_FILTER_KEYS or k not in meta_fields:
                errors.append(f"{q['id']}: filter key '{k}' khong hop le (cho phep: {sorted(meta_fields)})")
            elif not any(p["extra"].get(k) == v for p in pages):
                errors.append(f"{q['id']}: filter {k}={v!r} khong ton tai trong corpus")
            else:
                for t in targets[:1]:
                    pg = page_index.get((t["source_id"], str(t["page"])))
                    if pg and pg["extra"].get(k) != v:
                        errors.append(f"{q['id']}: filter {k}={v!r} loai mat gold page")
    n_dev = sum(q["split"] == "dev" for q in qs)
    n_test = sum(q["split"] == "test" for q in qs)
    n_flt = sum(bool(q.get("filter")) for q in qs)
    n_flt_test = sum(bool(q.get("filter")) and q["split"] == "test" for q in qs)
    if len(qs) < 20 or n_dev < 15 or n_test < 5:
        errors.append(f"Can >=20 cau, >=15 dev, >=5 test (hien: {len(qs)}, dev {n_dev}, test {n_test})")
    if n_flt < 3 or n_flt_test < 1:
        errors.append(f"Can >=3 cau co filter, >=1 thuoc test (hien: {n_flt}, test {n_flt_test})")
    if errors:
        raise PipelineError("Benchmark khong hop le:\n  - " + "\n  - ".join(errors))

    # khoa split truoc khi thu nghiem
    lock_path = cfg.root / "benchmark" / "split_lock.json"
    sig = sha256_text(json.dumps(sorted((q["id"], q["split"]) for q in qs)))
    if lock_path.exists() and not cfg.relock:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock["split_sha256"] != sig:
            raise PipelineError("Danh sach cau/split khac ban da khoa (benchmark/split_lock.json). "
                                "Khong sua test sau khi xem ket qua; neu bat buoc, chay --relock va ghi vao bao cao.")
    else:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"split_sha256": sig, "locked_at": now_iso(), "n": len(qs), "dev": n_dev, "test": n_test}
        if lock_path.exists():
            entry["relocked"] = True
            log.warning("RELOCK split: ghi chu ro trong bao cao")
        lock_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return qs


# --------------------------------------------------------------------------- chunking
def fixed_chunks(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    out, start, n = [], 0, len(text)
    step = max(1, size - overlap)
    while start < n:
        end = min(start + size, n)
        if text[start:end].strip():
            out.append((start, end))
        if end == n:
            break
        start += step
    return out


def _split_blocks(text: str) -> list[tuple[int, int]]:
    blocks, pos = [], 0
    for m in [*re.finditer(r"\n[ \t]*\n", text), None]:
        end = m.start() if m else len(text)
        seg = text[pos:end]
        if seg.strip():
            lead, trail = len(seg) - len(seg.lstrip()), len(seg) - len(seg.rstrip())
            blocks.append((pos + lead, end - trail))
        pos = m.end() if m else len(text)
    return blocks


def _hard_split(text: str, ps: int, pe: int, mx: int) -> list[tuple[int, int]]:
    out = []
    while pe - ps > mx:
        cut = text.rfind(" ", ps + 1, ps + mx)
        if cut <= ps:
            cut = ps + mx
        out.append((ps, cut))
        ps = cut
        while ps < pe and text[ps].isspace():
            ps += 1
    if ps < pe:
        out.append((ps, pe))
    return out


_SENT = re.compile(r"(?<=[.!?…])\s+")


def _split_oversize(text: str, s: int, e: int, mx: int) -> list[tuple[int, int]]:
    pieces, p = [], 0
    seg = text[s:e]
    for m in _SENT.finditer(seg):
        pieces.append((s + p, s + m.start()))
        p = m.end()
    pieces.append((s + p, e))
    flat = [x for ps, pe in pieces for x in _hard_split(text, ps, pe, mx)]
    merged, (cs, ce) = [], flat[0]
    for ps, pe in flat[1:]:
        if pe - cs <= mx:
            ce = pe
        else:
            merged.append((cs, ce))
            cs, ce = ps, pe
    merged.append((cs, ce))
    return merged


def _is_heading(t: str) -> bool:
    t = t.strip()
    if "\n" in t or len(t) > 80:
        return False
    return t.startswith("#") or not re.search(r"[.!?;,:…]$", t)


def paragraph_chunks(text: str, mx: int, mn: int) -> list[tuple[int, int]]:
    """Gop cac doan lien ke toi da `mx` ky tu; heading khong bi bo lai cuoi chunk;
    gap heading moi thi tach chunk neu chunk hien tai da >= `mn`. Chunk luon la doan lien tuc cua trang."""
    blocks: list[tuple[int, int, bool]] = []
    for s, e in _split_blocks(text):
        if e - s > mx:
            blocks += [(a, b, False) for a, b in _split_oversize(text, s, e, mx)]
        else:
            blocks.append((s, e, _is_heading(text[s:e])))
    out: list[tuple[int, int]] = []
    cur: list[tuple[int, int, bool]] = []

    def emit(c):
        if c:
            out.append((c[0][0], c[-1][1]))

    for b in blocks:
        if cur:
            too_big = b[1] - cur[0][0] > mx
            new_section = b[2] and (cur[-1][1] - cur[0][0]) >= mn
            if (too_big or new_section) and not all(x[2] for x in cur):
                carry = [cur.pop()] if (cur[-1][2] and len(cur) > 1) else []
                emit(cur)
                cur = carry
        cur.append(b)
    emit(cur)
    return out


def build_chunks(strategy: str, pages: list[dict], cfg) -> list[dict]:
    chunks: list[dict] = []
    for p in pages:
        spans = (fixed_chunks(p["text"], cfg.fixed_size, cfg.fixed_overlap) if strategy == "A"
                 else paragraph_chunks(p["text"], cfg.para_max, cfg.para_min))
        for i, (s, e) in enumerate(spans):
            text = p["text"][s:e]
            meta = {"document_id": p["document_id"], "source": p["source"], "page": p["page"],
                    "chunk_index": i, "char_start": s, "char_end": e, **p["extra"]}
            cid = f"{strategy}-{p['document_id']}-{p['page']}-{i:04d}-{sha256_text(text)[:8]}"
            chunks.append({"id": cid, "strategy": strategy, "text": text, "metadata": meta})
    if not chunks:
        raise PipelineError(f"Chien luoc {strategy} khong tao duoc chunk nao")
    return chunks


# --------------------------------------------------------------------------- embedding + store
class Embedder:
    def __init__(self, cfg):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise PipelineError("Thieu sentence-transformers: pip install sentence-transformers") from e
        try:
            self.model = SentenceTransformer(cfg.model_name, device=cfg.device)
        except Exception as e:  # tai model/mang loi -> bao loi, khong fallback
            raise PipelineError(f"Khong nap duoc embedding model '{cfg.model_name}': {e}") from e
        self.cfg = cfg
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        vecs = self.model.encode([prefix + t for t in texts], batch_size=self.cfg.batch_size,
                                 normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
        return vecs.tolist()

    def count_tokens(self, texts: list[str]) -> int:
        try:
            ids = self.model.tokenizer(texts, add_special_tokens=False)["input_ids"]
            return int(sum(len(x) for x in ids))
        except Exception:
            return -1


def get_client(cfg):
    try:
        import chromadb
    except ImportError as e:
        raise PipelineError("Thieu chromadb: pip install chromadb") from e
    cfg.store_dir.mkdir(parents=True, exist_ok=True)
    try:
        return chromadb.PersistentClient(path=str(cfg.store_dir))
    except Exception as e:
        raise PipelineError(f"Khong khoi tao duoc Chroma tai {cfg.store_dir}: {e}") from e


def make_collection(client, name: str):
    try:
        return client.create_collection(name=name, configuration={"hnsw": {"space": "cosine"}})
    except TypeError:  # ban Chroma cu
        return client.create_collection(name=name, metadata={"hnsw:space": "cosine"})


def chunk_stats(chunks: list[dict], tokens: int) -> dict:
    lens = [len(c["text"]) for c in chunks]
    srt = sorted(lens)
    return {"count": len(lens), "total_chars": sum(lens), "total_tokens": tokens,
            "mean": round(statistics.mean(lens), 1), "median": statistics.median(lens),
            "min": srt[0], "p90": srt[min(len(srt) - 1, int(0.9 * len(srt)))], "max": srt[-1]}


def strategy_fingerprint(strategy: str, pages: list[dict], cfg) -> str:
    params = {"A": [cfg.fixed_size, cfg.fixed_overlap], "B": [cfg.para_max, cfg.para_min],
              "C": [cfg.para_max, cfg.para_min]}[strategy]
    payload = [strategy, params, cfg.model_name, cfg.query_prefix, cfg.doc_prefix,
               [(p["document_id"], str(p["page"]), sha256_text(p["text"]), sorted(p["extra"].items())) for p in pages]]
    return sha256_text(json.dumps(payload, ensure_ascii=False, default=str))


def ingest(cfg, client, embedder: Embedder, pages: list[dict]) -> tuple[dict, dict, dict]:
    """Nap A/B/C. Tra ve (collections, chunks_by_strategy, ingest_info). Idempotent: id xac dinh + upsert."""
    state_path = cfg.results / "index_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    collections, all_chunks, info = {}, {}, {}
    vec_cache: dict[str, list[list[float]]] = {}
    for s in STRATEGIES:
        t0 = time.perf_counter()
        chunks = build_chunks(s, pages, cfg) if s != "C" else [
            {**c, "id": "C" + c["id"][1:], "strategy": "C"} for c in all_chunks["B"]]
        chunk_s = time.perf_counter() - t0
        all_chunks[s] = chunks
        fp = strategy_fingerprint(s, pages, cfg)
        name = f"{cfg.collection_prefix}_{s.lower()}"
        existing = None
        try:
            existing = client.get_collection(name)
        except Exception:
            pass
        st = state.get(s)
        if (existing is not None and st and st["fingerprint"] == fp and existing.count() == len(chunks)
                and not cfg.force_reingest):
            log.info("[%s] dung lai index hien co (%d chunk, cung phien ban)", s, len(chunks))
            collections[s], info[s] = existing, {**st, "reused_index": True}
            continue
        if existing is not None:
            client.delete_collection(name)  # tai lieu/cau hinh doi -> dung lai tu dau, khong nhan doi
        col = make_collection(client, name)
        ids = [c["id"] for c in chunks]
        te = time.perf_counter()
        reused_from = None
        if s == "C":
            if "B" in vec_cache:
                vecs = vec_cache["B"]
            else:
                got = collections["B"].get(ids=[c["id"] for c in all_chunks["B"]], include=["embeddings"])
                pos = {i: k for k, i in enumerate(got["ids"])}
                vecs = [[float(x) for x in got["embeddings"][pos[c["id"]]]] for c in all_chunks["B"]]
            reused_from = "B"  # cung chunk, cung model -> khong embed lai
        else:
            vecs = embedder.encode([c["text"] for c in chunks], cfg.doc_prefix)
            vec_cache[s] = vecs
        embed_s = time.perf_counter() - te
        tokens = embedder.count_tokens([c["text"] for c in chunks])
        ts = time.perf_counter()
        for i in range(0, len(chunks), 500):
            j = i + 500
            col.upsert(ids=ids[i:j], embeddings=vecs[i:j], documents=[c["text"] for c in chunks[i:j]],
                       metadatas=[c["metadata"] for c in chunks[i:j]])
        store_s = time.perf_counter() - ts
        # nap lai cung phien ban: so luong khong doi
        n1 = col.count()
        col.upsert(ids=ids[:500], embeddings=vecs[:500], documents=[c["text"] for c in chunks[:500]],
                   metadatas=[c["metadata"] for c in chunks[:500]])
        if n1 != len(chunks) or col.count() != n1:
            raise PipelineError(f"[{s}] kiem tra nap lai that bai: {n1}/{col.count()} != {len(chunks)} (nhan doi?)")
        tokens_billed = 0 if reused_from else max(tokens, 0)
        info[s] = {"fingerprint": fp, "chunk_stats": chunk_stats(chunks, tokens),
                   "ingest": {"chunk_s": round(chunk_s, 3), "embed_s": round(embed_s, 3), "store_s": round(store_s, 3),
                              "total_s": round(chunk_s + embed_s + store_s, 3),
                              "embeddings_reused_from": reused_from,
                              "cost_usd": round(tokens_billed * cfg.cost_per_1m_tokens / 1e6, 6)},
                   "reused_index": False}
        state[s] = {k: info[s][k] for k in ("fingerprint", "chunk_stats", "ingest")}
        collections[s] = col
        log.info("[%s] %d chunk, embed %.1fs, store %.1fs", s, len(chunks), embed_s, store_s)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return collections, all_chunks, info


# --------------------------------------------------------------------------- retrieval + eval
def to_where(flt: dict) -> dict | None:
    if not flt:
        return None
    items = [{k: v} for k, v in flt.items()]
    return items[0] if len(items) == 1 else {"$and": items}


def judge(q: dict, meta: dict, text: str) -> tuple[bool, bool]:
    """Tra ve (dung nguon/trang gold, hit = dung nguon/trang VA chunk chua du evidence)."""
    ft, page_match, hit = fold(text), False, False
    for t in q["_targets"]:
        if str(meta["document_id"]) == str(t["source_id"]) and str(meta["page"]) == str(t["page"]):
            page_match = True
            if fold(t["evidence_quote"]) in ft:
                hit = True
    return page_match, hit


def run_split(strategy: str, col, qs: list[dict], qvecs: dict[str, list[float]]) -> list[dict]:
    rows = []
    for q in qs:
        flt = q.get("filter") or {}
        applied = to_where(flt) if strategy == "C" else None  # C: chi loc khi cau hoi co filter
        t0 = time.perf_counter()
        res = col.query(query_embeddings=[qvecs[q["id"]]], n_results=TOP_K, where=applied,
                        include=["documents", "metadatas", "distances"])
        ms = (time.perf_counter() - t0) * 1000
        retrieved = []
        for r, (cid, doc, meta, dist) in enumerate(zip(res["ids"][0], res["documents"][0],
                                                       res["metadatas"][0], res["distances"][0]), 1):
            pm, hit = judge(q, meta, doc)
            ok_f = all(meta.get(k) == v for k, v in flt.items()) if flt else None
            retrieved.append({"rank": r, "chunk_id": cid, "score": round(1 - float(dist), 6), "text": doc,
                              "metadata": meta, "gold_source_page_match": pm, "evidence_full": hit,
                              "hit": hit, "filter_ok": ok_f})
        hits = [x["hit"] for x in retrieved]
        if any(hits):
            err = None if hits[0] else "hit_khong_o_top1"
        elif any(x["gold_source_page_match"] for x in retrieved):
            err = "dung_trang_nhung_thieu_evidence (evidence bi tach/cat)"
        else:
            err = "sai_nguon_hoac_trang"
        rows.append({"strategy": strategy, "question_id": q["id"], "split": q["split"], "type": q.get("type"),
                     "question": q["question"], "filter": flt or None, "applied_filter": applied,
                     "expected_answer": q["expected_answer"], "evidence_quote": q["evidence_quote"],
                     "gold": q["_targets"], "retrieved": retrieved, "hit@1": bool(hits and hits[0]),
                     "hit@3": any(hits), "error_type": err, "search_ms": round(ms, 2)})
    return rows


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    flt = [r for r in rows if r["filter"]]
    return {"n": n, "hit1": sum(r["hit@1"] for r in rows), "hit3": sum(r["hit@3"] for r in rows),
            "n_filter": len(flt),
            "filter_compliant": sum(all(x["filter_ok"] for x in r["retrieved"]) and bool(r["retrieved"]) for r in flt),
            "search_ms_total": round(sum(r["search_ms"] for r in rows), 2)}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def choose_strategy(dev: dict[str, dict], stats: dict[str, dict]) -> dict:
    """Tieu chi chot TRUOC khi xem test: Hit@3 dev (cao hon tot) -> Hit@1 dev -> it chunk hon -> thu tu A<B<C."""
    order = sorted(STRATEGIES, key=lambda s: (-dev[s]["hit3"], -dev[s]["hit1"], stats[s]["count"], s))
    return {"selected": order[0], "ranking": order,
            "criteria": "dev Hit@3 > dev Hit@1 > it chunk hon > thu tu chu cai",
            "dev": dev, "decided_at": now_iso(), "decided_before_test": True}


# --------------------------------------------------------------------------- manual-label samples
def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def prepare_manual(cfg, pages, chunks, test_rows, qs) -> None:
    man = cfg.results / "manual"
    # 1) Coherence: 10 page ID dau (da sap xep), vi tri = ky tu khong-trang dau tien tai/sau giua trang
    positions = []
    for p in pages[:10]:
        off = len(p["text"]) // 2
        while off < len(p["text"]) - 1 and p["text"][off].isspace():
            off += 1
        positions.append({"position_id": f"{p['document_id']}#{p['page']}", "p": p, "offset": off})
    coh = []
    for s in STRATEGIES:
        for pos in positions:
            p, off = pos["p"], pos["offset"]
            cover = [c for c in chunks[s] if c["metadata"]["document_id"] == p["document_id"]
                     and str(c["metadata"]["page"]) == str(p["page"])
                     and c["metadata"]["char_start"] <= off < c["metadata"]["char_end"]]
            best = max(cover, key=lambda c: min(off - c["metadata"]["char_start"], c["metadata"]["char_end"] - off),
                       default=None)  # nhieu chunk phu cung vi tri -> chunk "trung tam" nhat
            coh.append({"strategy": s, "position_id": pos["position_id"], "char_offset": off,
                        "chunk_id": best["id"] if best else "", "chunk_text": best["text"] if best else "",
                        "score_0_2": "", "reason": ""})
    write_csv(man / "coherence_labels.csv",
              ["strategy", "position_id", "char_offset", "chunk_id", "chunk_text", "score_0_2", "reason"], coh)

    # 2) 5 cau test: uu tien cau co filter, sau do theo id (xac dinh truoc khi xem diem)
    test_qs = sorted([q for q in qs if q["split"] == "test"], key=lambda q: (not q.get("filter"), str(q["id"])))
    picked = [q["id"] for q in test_qs[:5]]
    (cfg.results / "manual_sample_selection.json").write_text(json.dumps({
        "coherence_positions": [p["position_id"] for p in positions], "coherence_rule": "10 page ID dau da sap xep; "
        "vi tri = ky tu khong-trang dau tien tu giua trang; nhieu chunk phu -> chunk trung tam nhat",
        "test_questions": picked, "test_rule": "cau test co filter truoc, sau do theo id; lay 5 cau",
        "created_at": now_iso()}, indent=2, ensure_ascii=False), encoding="utf-8")
    page_text = {(p["document_id"], str(p["page"])): p for p in pages}
    meta_rows, ground_rows = [], []
    for s in STRATEGIES:
        for r in (x for x in test_rows[s] if x["question_id"] in picked):
            for it in r["retrieved"]:
                m = it["metadata"]
                pg = page_text.get((m["document_id"], str(m["page"])))
                meta_rows.append({
                    "strategy": s, "question_id": r["question_id"], "rank": it["rank"], "chunk_id": it["chunk_id"],
                    "metadata_json": json.dumps(m, ensure_ascii=False),
                    "auto_source_ok": bool(pg and pg["source"] == m["source"]),
                    "auto_page_ok": bool(pg and fold(it["text"]) in fold(pg["text"])),
                    "auto_fields_ok": bool(pg and all(m.get(k) == v for k, v in pg["extra"].items())),
                    "metadata_complete_correct": "", "note": ""})
            ground_rows.append({
                "strategy": s, "question_id": r["question_id"], "question": r["question"],
                "expected_answer": r["expected_answer"], "evidence_quote": r["evidence_quote"],
                "top3": "\n---\n".join(f"[{i['rank']}] {i['metadata']['document_id']} p.{i['metadata']['page']} "
                                       f"(score {i['score']})\n{i['text']}" for i in r["retrieved"]),
                "grounding_0_2": "", "reason": ""})
    write_csv(man / "metadata_audit.csv", ["strategy", "question_id", "rank", "chunk_id", "metadata_json",
              "auto_source_ok", "auto_page_ok", "auto_fields_ok", "metadata_complete_correct", "note"], meta_rows)
    write_csv(man / "grounding_labels.csv", ["strategy", "question_id", "question", "expected_answer",
              "evidence_quote", "top3", "grounding_0_2", "reason"], ground_rows)
    log.info("Mau cham tay -> %s (dien score/reason roi chay: python run.py summarize)", man)


# --------------------------------------------------------------------------- reporting
def env_versions(cfg, embedder_dim: int | None = None) -> dict:
    v = {"python": sys.version.split()[0]}
    for pkg in ("chromadb", "sentence-transformers", "torch", "numpy"):
        try:
            v[pkg] = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            v[pkg] = None
    return v


def append_experiment_log(cfg, run_id, manifest_hash, bench_hash, dim, info, dev, test, versions) -> None:
    path = cfg.artifacts / "experiment_log.csv"
    cols = ["run_id", "timestamp", "strategy", "hypothesis", "changed_factor", "chunking", "filter_policy",
            "embedding_model", "dim", "vector_store", "metric", "benchmark_sha256", "corpus_sha256",
            "chunk_count", "ingest_seconds", "cost_usd", "dev_hit1", "dev_hit3", "test_hit1", "test_hit3"]
    chunking = {"A": f"fixed {cfg.fixed_size}/{cfg.fixed_overlap}", "B": f"paragraph max{cfg.para_max} min{cfg.para_min}",
                "C": f"paragraph max{cfg.para_max} min{cfg.para_min}"}
    rows = []
    for s in STRATEGIES:
        rows.append({"run_id": run_id, "timestamp": now_iso(), "strategy": s, "hypothesis": HYPOTHESES[s][0],
                     "changed_factor": HYPOTHESES[s][1], "chunking": chunking[s],
                     "filter_policy": "none" if s != "C" else "only when question has filter",
                     "embedding_model": cfg.model_name, "dim": dim,
                     "vector_store": f"chroma {versions['chromadb']} (HNSW approximate)", "metric": "cosine",
                     "benchmark_sha256": bench_hash, "corpus_sha256": manifest_hash,
                     "chunk_count": info[s]["chunk_stats"]["count"], "ingest_seconds": info[s]["ingest"]["total_s"],
                     "cost_usd": info[s]["ingest"]["cost_usd"],
                     "dev_hit1": f"{dev[s]['hit1']}/{dev[s]['n']}", "dev_hit3": f"{dev[s]['hit3']}/{dev[s]['n']}",
                     "test_hit1": f"{test[s]['hit1']}/{test[s]['n']}", "test_hit3": f"{test[s]['hit3']}/{test[s]['n']}"})
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


def cmd_validate(cfg) -> tuple:
    manifest, pages, warns = load_corpus(cfg)
    qs = load_questions(cfg, pages, manifest)
    for w in warns:
        log.warning(w)
    log.info("OK: %d trang, %d cau (%d dev / %d test, %d co filter)", len(pages), len(qs),
             sum(q["split"] == "dev" for q in qs), sum(q["split"] == "test" for q in qs),
             sum(bool(q.get("filter")) for q in qs))
    return manifest, pages, qs


def cmd_run(cfg) -> None:
    random.seed(cfg.seed)
    manifest, pages, qs = cmd_validate(cfg)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    cfg.results.mkdir(parents=True, exist_ok=True)
    (cfg.results / "selection_criteria.json").write_text(json.dumps({
        "written_at": now_iso(), "rule": "dev Hit@3 > dev Hit@1 > it chunk hon > thu tu chu cai",
        "note": "Chot tren dev truoc khi chay test. Coherence/Grounding cham tay bao cao rieng, khong dung de chon."},
        indent=2), encoding="utf-8")
    try:
        import numpy as np
        np.random.seed(cfg.seed)
        import torch
        torch.manual_seed(cfg.seed)
    except ImportError:
        pass
    embedder = Embedder(cfg)
    client = get_client(cfg)
    collections, chunks, info = ingest(cfg, client, embedder, pages)

    t0 = time.perf_counter()
    qvec_list = embedder.encode([q["question"] for q in qs], cfg.query_prefix)
    qembed_s = time.perf_counter() - t0
    qvecs = {q["id"]: v for q, v in zip(qs, qvec_list)}

    dev_qs = [q for q in qs if q["split"] == "dev"]
    test_qs = [q for q in qs if q["split"] == "test"]
    dev_rows = {s: run_split(s, collections[s], dev_qs, qvecs) for s in STRATEGIES}
    for s in STRATEGIES:
        write_jsonl(cfg.results / "traces" / f"{s}_dev.jsonl", dev_rows[s])
    dev_m = {s: metrics(dev_rows[s]) for s in STRATEGIES}
    stats = {s: info[s]["chunk_stats"] for s in STRATEGIES}
    sel = choose_strategy(dev_m, stats)
    (cfg.results / "selected_strategy.json").write_text(json.dumps(sel, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Chot chien luoc (truoc khi chay test): %s", sel["selected"])

    test_rows = {s: run_split(s, collections[s], test_qs, qvecs) for s in STRATEGIES}
    for s in STRATEGIES:
        write_jsonl(cfg.results / "traces" / f"{s}_test.jsonl", test_rows[s])
    test_m = {s: metrics(test_rows[s]) for s in STRATEGIES}

    bench_hash = sha256_text(cfg.questions.read_text(encoding="utf-8"))
    corpus_hash = sha256_text(json.dumps([(p["document_id"], str(p["page"]), sha256_text(p["text"])) for p in pages]))
    versions = env_versions(cfg)
    out = {"run_id": run_id, "finished_at": now_iso(), "seed": cfg.seed, "model": cfg.model_name,
           "dim": embedder.dim, "query_embed_seconds": round(qembed_s, 3), "versions": versions,
           "vector_store": {"name": "chroma", "path": str(cfg.store_dir), "metric": "cosine",
                            "search": "HNSW (approximate)"},
           "config": {"A": {"size": cfg.fixed_size, "overlap": cfg.fixed_overlap},
                      "B": {"max": cfg.para_max, "min_new_section": cfg.para_min},
                      "C": "same chunks as B + where-filter only for filtered questions"},
           "benchmark_sha256": bench_hash, "corpus_sha256": corpus_hash, "ingest": info,
           "dev": dev_m, "test": test_m, "selected": sel["selected"]}
    (cfg.results / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    append_experiment_log(cfg, run_id, corpus_hash, bench_hash, embedder.dim, info, dev_m, test_m, versions)
    prepare_manual(cfg, pages, chunks, test_rows, qs)
    for s in STRATEGIES:
        log.info("[%s] dev Hit@1 %d/%d Hit@3 %d/%d | test Hit@1 %d/%d Hit@3 %d/%d", s,
                 dev_m[s]["hit1"], dev_m[s]["n"], dev_m[s]["hit3"], dev_m[s]["n"],
                 test_m[s]["hit1"], test_m[s]["n"], test_m[s]["hit3"], test_m[s]["n"])


def read_scores(path: Path, col: str, key: str = "strategy") -> dict[str, list[float]]:
    out: dict[str, list[float]] = {s: [] for s in STRATEGIES}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = (r.get(col) or "").strip()
            if v != "":
                out[r[key]].append(float(v))
    return out


def cmd_summarize(cfg) -> None:
    mp = cfg.results / "metrics.json"
    if not mp.exists():
        raise PipelineError("Chua co results/metrics.json - chay `python run.py run` truoc")
    m = json.loads(mp.read_text(encoding="utf-8"))
    man = cfg.results / "manual"
    coh = read_scores(man / "coherence_labels.csv", "score_0_2")
    mdc = read_scores(man / "metadata_audit.csv", "metadata_complete_correct")
    grd = read_scores(man / "grounding_labels.csv", "grounding_0_2")
    L = ["| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thoi gian | Chi phi |",
         "|---|---|---|---|---|---|---|"]
    for s in STRATEGIES:
        ing = m["ingest"][s]
        for sp in ("dev", "test"):
            x = m[sp][s]
            L.append(f"| {s} | {sp} | {x['hit1']}/{x['n']} | {x['hit3']}/{x['n']} | {ing['chunk_stats']['count']} | "
                     f"ingest {ing['ingest']['total_s']}s; search {x['search_ms_total']}ms | ${ing['ingest']['cost_usd']} |")
    L += ["", "| Strategy | Coherence trung binh /2 (n) | Metadata du va dung (n/N) | Filter tuan thu (test) | "
          "Grounding 0/1/2 va trung binh /2 |", "|---|---|---|---|---|"]
    for s in STRATEGIES:
        c, d, g = coh[s], mdc[s], grd[s]
        t = m["test"][s]
        coh_s = f"{statistics.mean(c):.2f} (n={len(c)})" if c else "chua cham"
        md_s = f"{int(sum(d))}/{len(d)}" if d else "chua cham"
        fl_s = f"{t['filter_compliant']}/{t['n_filter']}" if t["n_filter"] else "N/A"
        gr_s = (f"{sum(v == 0 for v in g)}/{sum(v == 1 for v in g)}/{sum(v == 2 for v in g)}; "
                f"{statistics.mean(g):.2f}") if g else "chua cham"
        L.append(f"| {s} | {coh_s} | {md_s} | {fl_s} | {gr_s} |")
    L += ["", "| Strategy | chunks | mean chars | median | p90 | max | tong chars | tong tokens |",
          "|---|---|---|---|---|---|---|---|"]
    for s in STRATEGIES:
        c = m["ingest"][s]["chunk_stats"]
        L.append(f"| {s} | {c['count']} | {c['mean']} | {c['median']} | {c['p90']} | {c['max']} | "
                 f"{c['total_chars']} | {c['total_tokens']} |")
    L += ["", f"Chien luoc chot tren dev: **{m['selected']}** (xem results/selected_strategy.json).",
          f"Model: {m['model']} (dim {m['dim']}); Chroma {m['versions']['chromadb']}; seed {m['seed']}; "
          f"chay luc {m['finished_at']}.",
          "Luu y: nhan thu cong do tac gia cham (nguy co thien lenh); test nho, chi mang tinh minh hoa."]
    (cfg.results / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def _outline_entries(reader) -> list[dict]:
    flat: list[dict] = []

    def walk(items, level):
        for it in items:
            if isinstance(it, list):
                walk(it, level + 1)
            else:
                try:
                    pg = reader.get_destination_page_number(it) + 1
                except Exception:
                    continue
                flat.append({"level": level, "title": re.sub(r"\s+", " ", str(it.title)).strip(),
                             "page": pg, "order": len(flat)})
    walk(reader.outline, 0)
    keep, skipping = [], False
    for e in flat:  # bo muc danh sach Boxes/Figures/Tables va con cua chung
        if e["level"] == 0:
            skipping = e["title"] in {"Boxes", "Figures", "Tables"}
        if not skipping:
            keep.append(e)
    return keep


def strip_running_header(text: str) -> str:
    """Bo dong dau trang (header chay) + so trang in: dong 1 ngan, dong 2 chi la so. Chi ap dung dau trang."""
    lines = text.split("\n")
    if len(lines) > 2 and len(lines[0]) <= 90 and re.fullmatch(r"\d{1,3}", lines[1].strip()):
        lines = lines[2:]
    elif len(lines) > 2 and re.fullmatch(r"\d{1,3}", lines[0].strip()) and len(lines[1]) <= 90 \
            and lines[1].isupper():
        lines = lines[2:]
    return "\n".join(lines).strip()


def cmd_build_corpus(cfg) -> None:
    """PDF -> data/pages.jsonl. page = so trang PDF vat ly (1-based). chapter/section lay tu outline PDF
    (khong doan). Cap nhat manifest (co .bak) cho khop so trang thuc te."""
    try:
        import pypdf
    except ImportError as e:
        raise PipelineError("Thieu pypdf: pip install pypdf") from e
    raw = json.loads(cfg.manifest.read_text(encoding="utf-8"))
    man = normalize_manifest(raw)
    src = man["sources"][0]
    cands = [cfg.pdf] if cfg.pdf else []
    cands += [cfg.root / "data" / src["source_id"], cfg.root / src["source_id"], Path.cwd() / src["source_id"]]
    pdf = next((c for c in cands if c and Path(c).exists()), None)
    if pdf is None:
        raise PipelineError(f"Khong thay PDF {src['source_id']}; dung --pdf <duong dan>")
    reader = pypdf.PdfReader(str(pdf))
    entries = _outline_entries(reader)
    if not entries:
        raise PipelineError("PDF khong co outline - khong the gan section tin cay; hay tu gan metadata")
    exclude = {x.strip() for x in cfg.exclude_sections.split(",") if x.strip()}
    cfg.results.mkdir(parents=True, exist_ok=True)
    rows, excluded, errs = [], {"outline:" + x: 0 for x in exclude} | {"short_or_blank": 0}, []
    for i, pg in enumerate(reader.pages, 1):
        before = [e for e in entries if e["page"] <= i]
        sec = max(before, key=lambda e: (e["page"], e["order"]))["title"] if before else None
        ch = max((e for e in before if e["level"] == 0), key=lambda e: (e["page"], e["order"]), default=None)
        try:
            text = strip_running_header(normalize_text(pg.extract_text() or ""))
        except Exception as e:
            errs.append({"page": i, "error": repr(e)})
            continue
        if sec in exclude:
            excluded["outline:" + sec] += 1
        elif len(text) <= cfg.min_chars:
            excluded["short_or_blank"] += 1
        else:
            rows.append({"document_id": src["source_id"], "source": src["source_id"], "page": i, "text": text,
                         "chapter": ch["title"] if ch else None, "section": sec})
    cfg.pages.parent.mkdir(parents=True, exist_ok=True)
    with cfg.pages.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if errs:
        write_jsonl(cfg.results / "build_corpus_errors.jsonl", errs)
    # dong bo manifest (giu ban sao .bak)
    bak = cfg.manifest.with_suffix(".json.bak")
    if not bak.exists():
        bak.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    new = dict(raw)
    new.update({"total_pages": len(reader.pages), "counted_pages": len(rows),
                "counted_page_ids": [r["page"] for r in rows],
                "page_numbering": "so trang PDF vat ly, bat dau tu 1",
                "page_counting_rules": f"Trang co > {cfg.min_chars} ky tu van ban sau chuan hoa NFC va bo header chay + so trang in dau trang; loai cac muc outline "
                                       f"{sorted(exclude)} va trang trong. Trang bo qua: {excluded}",
                "metadata_fields": {
                    "chapter": {"type": "str", "source": "muc outline cap 1 cua PDF bao phu trang",
                                "reason": "loc theo chuong/phan (Chapter 1-3, Part 2, Appendix...)"},
                    "section": {"type": "str", "source": "muc outline sau nhat bao phu trang",
                                "reason": "loc theo tieu muc/ho so quoc gia; truy vet noi dung"}}})
    if isinstance(raw.get("metadata_fields"), list):
        new["metadata_fields_declared_but_unused"] = [x for x in raw["metadata_fields"]
                                                      if x not in FORBIDDEN_FILTER_KEYS | {"chapter", "section"}]
    cfg.manifest.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Da ghi %d trang -> %s (tong %d trang PDF; bo qua %s; loi %d). Manifest da cap nhat (ban cu: %s)",
             len(rows), cfg.pages, len(reader.pages), excluded, len(errs), bak.name)
    log.info("Chua co year/income_group/region/category: PDF khong cung cap theo trang, khong tu doan.")


def main() -> None:
    cfg = parse_args()
    cfg.results.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(cfg.results / "run.log", encoding="utf-8")])
    try:
        {"build-corpus": cmd_build_corpus, "validate": cmd_validate, "run": cmd_run,
         "summarize": cmd_summarize}[cfg.command](cfg)
    except PipelineError as e:
        log.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
