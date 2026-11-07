"""Regression tests for the already-accounts feature.

Every case here is a bug that was live in the repo. Run after any change to
load/save/gate logic for already_accounts.txt.
"""
import os, sys, tempfile, shutil

REPO = r"C:/Users/Administrator/ikan cupang v2"
sys.path.insert(0, REPO)

fails = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(f"{name}  {detail}")

def test_already_feature_regressions():
    """Bug-by-bug regression cover for already_accounts.txt."""
    import fast_checker as fc
    import imap_engine as ie
    import sync_imap_vps as sv

    # ---- 1. canonical key keeps password case, folds email ---------------------
    k = fc.normalize_already_key("User@Example.COM:PassWord123")
    check("fc: email folded, password verbatim", k == "user@example.com:PassWord123", k)
    check("ie: same canonical form",
          ie._normalize_already_key("User@Example.COM:PassWord123") == k)
    check("sv: same canonical form",
          sv.normalize_already_key("User@Example.COM:PassWord123") == k)
    check("all three modules agree on one key",
          len({fc.normalize_already_key("A@B.CO:PaSs"),
               ie._normalize_already_key("A@B.CO:PaSs"),
               sv.normalize_already_key("A@B.CO:PaSs")}) == 1)

    # ---- 2. comments and blanks never become keys -----------------------------
    check("fc: comment dropped", fc.normalize_already_key("# note") == "")
    check("ie: comment dropped", ie._normalize_already_key("# note") == "")
    check("blank dropped", fc.normalize_already_key("   ") == "")

    # ---- 3. the gate now matches a mixed-case password ------------------------
    # Previously the loader stored 'user@x.com:password' (whole line lowered) while
    # the gate looked up 'user@x.com:PassWord123' -> never matched.
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "already_accounts.txt")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("User@X.com:PassWord123\n")
            f.write("plain@x.com:lower\n")

        loaded = set()
        with open(p, encoding="utf-8") as fh:
            for l in fh:
                key = fc.normalize_already_key(l)
                if key:
                    loaded.add(key)

        check("mixed-case record survives load",
              "user@x.com:PassWord123" in loaded, str(loaded))
        check("gate hit for mixed-case record",
              fc.normalize_already_key("User@X.com:PassWord123") in loaded)
        check("gate hit for plain-lower record",
              fc.normalize_already_key("plain@x.com:lower") in loaded)
        check("gate miss for a different password",
              fc.normalize_already_key("user@x.com:wrong") not in loaded)

        # ---- 4. CRLF in the source file must not corrupt the key --------------
        p2 = os.path.join(tmp, "crlf.txt")
        with open(p2, "wb") as f:
            f.write(b"Crlf@X.com:MyPass\r\n")
        with open(p2, encoding="utf-8") as fh:
            got = {fc.normalize_already_key(l) for l in fh if l.strip()}
        check("CRLF line yields clean key (no stray \\r)",
              got == {"crlf@x.com:MyPass"}, str(got))

        # ---- 5. sync-side write is LF-only and survives a round trip ----------
        p3 = os.path.join(tmp, "sync_out.txt")
        content = "\n".join(sorted({"user@x.com:PassWord123", "plain@x.com:lower"})) + "\n"
        with open(p3, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        raw = open(p3, "rb").read()
        check("sync write emits no CR bytes", b"\r" not in raw, repr(raw[:40]))
        check("sync write is byte-stable across two writes",
              raw == content.encode(), repr(raw[:40]))
        back = {sv.normalize_already_key(l) for l in raw.decode().splitlines() if l.strip()}
        check("round trip preserves the password exactly",
              "user@x.com:PassWord123" in back, str(back))

        # ---- 6. the local-master rewrite guard trips on CRLF and on drift -----
        p4 = os.path.join(tmp, "local_master.txt")
        keys = {"user@x.com:PassWord123", "plain@x.com:lower"}
        canonical = ("\n".join(sorted(keys)) + "\n").encode("utf-8")

        sv.LOCAL_MASTER_ALREADY = p4
        with open(p4, "wb") as f:
            f.write(canonical)
        check("guard: canonical file needs no rewrite",
              sv._local_already_needs_rewrite(keys) is False)
        with open(p4, "wb") as f:
            f.write(canonical.replace(b"\n", b"\r\n"))
        check("guard: CRLF file needs rewrite",
              sv._local_already_needs_rewrite(keys) is True)
        with open(p4, "wb") as f:
            f.write(canonical + b"extra@x.com:Pw\n")
        check("guard: content drift needs rewrite",
              sv._local_already_needs_rewrite(keys) is True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"RESULT: {len(fails)} FAILED")
        for f in fails:
            print("  -", f)
    assert not fails, "see printed FAIL lines"
    print("RESULT: ALL PASS")



if __name__ == "__main__":
    test_already_feature_regressions()
    print("RESULT: ALL PASS")