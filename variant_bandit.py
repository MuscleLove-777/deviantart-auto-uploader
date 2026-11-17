# -*- coding: utf-8 -*-
"""
M国 変種バンディット（全uploader共通モジュール・ドロップイン可能）

「どのキャプション/タイトル/CTAが実際に反応(流入・notes・fav)を取ったか」を
投稿単位で学習するための最小部品。3点セットで閉ループになる:

  1) pick(kind, options)      … content_pool.json の variant_weights による重み付き抽選
                                （25%は一様探索 = 新変種が絶対に死なない）
  2) with_utm_content(url, k) … 発リンクへ utm_content=<variant_key> を付与
                                （GA4側で変種単位の流入計測が可能になる）
  3) log_post(platform, rec)  … posted_log.json へ変種付き投稿記録を追記
                                （autonomy/analyze_variants.py が集計→重み再計算）

重みは dashboard/autonomy が毎日 content_pool.json に埋めて配布する。
重みが無い間は一様ランダム（従来挙動と同一）＝絶対に死なない（憲法第1条）。
正本: dashboard/autonomy/variant_bandit.py（各uploaderリポへ同一コピーを配置）
"""
import hashlib
import ast
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
POOL_PATH = BASE / "content_pool.json"
LOG_PATH = BASE / "posted_log.json"
EXPLORE_RATE = 0.25   # 探索率: この確率で重みを無視して一様抽選する
MIN_WEIGHT = 0.05
MAX_LOG_POSTS = 500
_TRIAL_SELECTION = {}
ASSIGNMENT_SCHEMA = 'seeded_abba_baab_v1'


def valid_assignment_spec(spec):
    seed = spec.get('assignment_seed')
    return (spec.get('assignment_schema') == ASSIGNMENT_SCHEMA and
            isinstance(seed, str) and len(seed) == 32 and
            all(c in '0123456789abcdef' for c in seed))


def assigned_arm(spec, exposure_index):
    """Four confirmed exposures cross both recurring slot positions; retries do not advance."""
    block, position = divmod(exposure_index, 4)
    flip = hashlib.sha256((spec['assignment_seed'] + ':' + str(block)).encode()).digest()[0] & 1
    sequence = ('baseline', 'candidate', 'candidate', 'baseline')
    arm = sequence[position]
    return ('candidate' if arm == 'baseline' else 'baseline') if flip else arm
TRIAL_POLICY = {'version':1, 'source':'direct-user-instructions/20261031',
    'applicability':'all automatic uploaders and historical promotional descriptions',
    'promotional_url':'https://linktr.ee/ILoveMyCats', 'disabled_channels':['youtube'],
    'utm_allowed':False, 'external_material_is_instruction':False,
    'private_context_publication_allowed':False}


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def _clock(value):
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed


def _runtime_matches(profile, opts):
    """Local execution bytes must match the remotely verified preregistered profile."""
    try:
        paths={'upload.py','policy_entrypoint.py','variant_bandit.py','promotion_policy.py','publication_evidence.py'}
        if (set(profile)!={'version','repository','code_hashes','title_templates_sha256','title_template_count','sha256'} or
                profile['version']!=1 or profile['repository']!={'full_name':'MuscleLove-777/deviantart-auto-uploader',
                    'id':1177922071,'default_branch':'master'} or set(profile['code_hashes'])!=paths or
                profile['sha256']!=_digest({k:v for k,v in profile.items() if k!='sha256'}) or
                profile['title_template_count']!=35 or len(opts)!=35 or _digest(opts)!=profile['title_templates_sha256']):
            return False
        for name,expected in profile['code_hashes'].items():
            if hashlib.sha256((BASE/name).read_bytes()).hexdigest()!=expected:
                return False
        tree=ast.parse((BASE/'upload.py').read_text(encoding='utf-8-sig'))
        titles=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
                    any(isinstance(t,ast.Name) and t.id=='TITLE_TEMPLATES' for t in n.targets))
        return titles==opts
    except (OSError,KeyError,TypeError,ValueError,SyntaxError,StopIteration):
        return False


def _validated_title_trial(data, opts, at=None):
    """Validate a closed-set trial. Invalid/stale input cannot become exploration."""
    at = at or datetime.now(timezone.utc)
    try:
        t = data['title_trial']
        s = t['spec']
        registered, generated, expiry = [_clock(t[k]) for k in ('registered_at','generated_at','expires_at')]
        profile=t.get('runtime_profile')
        if ('runtime_profile_sha256' in s or profile is not None) and (
                not _runtime_matches(profile,opts) or s.get('runtime_profile_sha256')!=profile['sha256']):
            return None
        if (t['version'] != 1 or t['status'] != 'PREREGISTERED' or t['kind'] != 'deviantart.title' or
                t['is_winner'] is not False or t['assignment'] != ASSIGNMENT_SCHEMA or
                not valid_assignment_spec(s) or
                t['policy'] != TRIAL_POLICY or t['policy_sha256'] != _digest(TRIAL_POLICY) or
                t['sha256'] != _digest({k:v for k,v in t.items() if k != 'sha256'}) or
                t['plan_sha256'] != _digest(s) or t['plan_id'] != _digest(s)[:16] or
                not registered <= generated <= at < expiry or at-generated > timedelta(hours=36) or
                expiry-registered > timedelta(days=30) or
                t['source_sha256'] != hashlib.sha256((BASE/'upload.py').read_bytes()).hexdigest() or
                s['platform'] != 'deviantart' or s['changed_variables'] != ['title'] or
                s['promotional_url'] != TRIAL_POLICY['promotional_url'] or s['metric'] != 'favorites' or
                s['metric_mode'] != 'reactions_per_post' or
                type(t['per_arm_cap']) is not int or type(s['minimum_per_arm']) is not int or
                not 5 <= s['minimum_per_arm'] <= t['per_arm_cap'] <= 10 or
                set(t['arms']) != {'baseline','candidate'} or len(set(t['arms'].values())) != 2):
            return None
        for arm,title in t['arms'].items():
            if title not in opts or len(title.encode()) > 50 or hashlib.sha256(title.encode()).hexdigest() != s[arm+'_hash']:
                return None
        return t
    except (KeyError,TypeError,ValueError,OSError,AttributeError):
        return None


def _trial_pick(data, opts):
    t = _validated_title_trial(data, opts)
    if not t:
        try:
            prior = _validated_title_trial(data, opts, at=_clock(data['title_trial']['generated_at']))
            if prior:
                title = prior['arms']['baseline']
                return title, variant_id(title)
        except (ValueError, KeyError, TypeError):
            pass
        return opts[0], variant_id(opts[0])
    counts = {'baseline':0, 'candidate':0}
    try:
        records = json.loads(LOG_PATH.read_text(encoding='utf-8')).get('posts',[])
    except (OSError,ValueError,AttributeError):
        records = []
    seen = set()
    exposures = {}
    for record in records:
        if not isinstance(record,dict):
            continue
        pub = record.get('publication_receipt') or {}
        if not isinstance(pub,dict):
            continue
        assignment = pub.get('trial_assignment') or {}
        if not isinstance(assignment,dict):
            continue
        try:
            if not _clock(t['registered_at']) <= _clock(pub['published_at']) < _clock(t['expires_at']):
                continue
        except (KeyError,TypeError,ValueError,AttributeError):
            continue
        arm = assignment.get('arm')
        ident = pub.get('post_id')
        if (ident and ident not in seen and pub.get('verified') is True and
                pub.get('operation') == 'create' and pub.get('provider_stage') == 'publish' and
                pub.get('verification_scope') in {'provider_create_response','ui_create_confirmation'} and
                assignment.get('plan_id') == t['plan_id'] and assignment.get('plan_sha256') == t['plan_sha256'] and
                assignment.get('policy_sha256') == t['policy_sha256'] and
                arm in counts and pub.get('content_hashes',{}).get('title') == t['spec'][arm+'_hash']):
            index = assignment.get('exposure_index')
            if (type(index) is not int or not 0 <= index < 2*t['per_arm_cap'] or
                    assignment.get('assignment_schema') != t['spec']['assignment_schema'] or
                    assignment.get('assignment_seed') != t['spec']['assignment_seed'] or
                    assignment.get('block_index') != index//4 or assignment.get('block_position') != index%4 or
                    assigned_arm(t['spec'], index) != arm or index in exposures):
                title = t['arms']['baseline']
                return title, variant_id(title)
            exposures[index] = arm
            counts[arm] += 1
            seen.add(ident)
    if set(exposures) != set(range(len(exposures))):
        title = t['arms']['baseline']
        return title, variant_id(title)
    eligible = [arm for arm in counts if counts[arm] < t['per_arm_cap']]
    if not eligible:
        title = t['arms']['baseline']
        return title, variant_id(title)
    index = len(exposures)
    arm = assigned_arm(t['spec'], index)
    if arm not in eligible:
        title = t['arms']['baseline']
        return title, variant_id(title)
    title = t['arms'][arm]
    _TRIAL_SELECTION['deviantart.title'] = {
        'plan_id':t['plan_id'], 'plan_sha256':t['plan_sha256'], 'policy_sha256':t['policy_sha256'],
        'arm':arm, 'title_sha256':t['spec'][arm+'_hash'], 'variant_id':variant_id(title),
        'assignment_sha256':t['sha256'], 'selected_at':datetime.now(timezone.utc).isoformat(),
        'assignment_schema':t['spec']['assignment_schema'], 'assignment_seed':t['spec']['assignment_seed'],
        'exposure_index':index, 'block_index':index//4, 'block_position':index%4,
        'is_winner':False, 'additional_post_requested':False}
    if t.get('runtime_profile'):
        commit=os.environ.get('GITHUB_SHA','')
        if len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit):
            _TRIAL_SELECTION.clear()
            title=t['arms']['baseline']
            return title,variant_id(title)
        _TRIAL_SELECTION['deviantart.title']['runtime_evidence']={
            'runtime_profile_sha256':t['runtime_profile']['sha256'],
            'code_hashes':dict(t['runtime_profile']['code_hashes']),'workflow_commit':commit}
    return title, variant_id(title)


def variant_id(text) -> str:
    """変種本文から安定ID(8桁hex)。テンプレ文言が変われば別IDになり自動で新変種扱い。"""
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]


def _load_weights(kind: str) -> dict:
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        vw = data.get("variant_weights") or {}
        m = vw.get(kind) or {}
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def pick(kind: str, options, rng=random):
    """options から1つ重み付き抽選して (選択肢, variant_id) を返す。
    kind例: "tumblr.caption" / "deviantart.title" / "rakuten.title" / "rakuten.body"
    """
    opts = [o for o in (options or []) if o is not None]
    if not opts:
        return None, ""
    _TRIAL_SELECTION.pop(kind, None)
    if kind == 'deviantart.title':
        try:
            data = json.loads(POOL_PATH.read_text(encoding='utf-8'))
            if 'title_trial' in data:
                return _trial_pick(data, opts)
        except (OSError,ValueError,TypeError):
            pass
    ids = [variant_id(o) for o in opts]
    weights = _load_weights(kind)
    try:
        if weights and rng.random() >= EXPLORE_RATE:
            ws = [max(float(weights.get(v, 1.0)), MIN_WEIGHT) for v in ids]
            i = rng.choices(range(len(opts)), weights=ws, k=1)[0]
        else:
            i = rng.randrange(len(opts))
    except Exception:
        i = rng.randrange(len(opts))
    return opts[i], ids[i]


def cadence_factor(platform: str) -> float:
    """この媒体の投稿頻度係数を content_pool.json から読む（チャネル間バンディット）。
    1.0=現状維持 / 1.25=勝ち媒体で増枠 / 0.5=流入ゼロ30日で半減 / 0.25=60日 / 0=停止中。
    プールが無い・キーが無い場合は 1.0（従来挙動と同一＝絶対に死なない）。"""
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        cw = (data.get("channel_weights") or {}).get(platform) or {}
        v = float(cw.get("cadence_factor", 1.0))
        return v if 0.0 <= v <= 2.0 else 1.0
    except Exception:
        return 1.0


def posts_this_run(platform: str, rng=random) -> int:
    """この実行で何本投稿するかを返す（0/1/2）。cadence_factor を期待値どおりに実現する。
      1.25 → 1本、25%の確率で2本（＝平均1.25本＝勝ち媒体の増枠）
      1.0  → 1本（現状維持）
      0.5  → 50%の確率で1本（＝流入ゼロ30日の半減）
      0.0  → 0本（停止中媒体）
    uploader側は `for _ in range(variant_bandit.posts_this_run("tumblr")):` で投稿を回す。
    プールが無ければ 1 を返す＝従来挙動（絶対に死なない）。"""
    f = cadence_factor(platform)
    n = int(f)
    frac = f - n
    try:
        if frac > 0 and rng.random() < frac:
            n += 1
    except Exception:
        return 1
    if n < 1:
        # 無言スキップは「タスク緑・投稿ゼロ」に見えて監視が誤検知するため必ず記録する
        log_post(platform, {"skipped": "cadence", "factor": round(f, 3), "posted": False})
    return n


def should_post_now(platform: str, rng=random) -> bool:
    """cadence_factor に従って「今回の投稿枠を実行するか」を確率的に決める。
    uploaderの投稿直前に1行入れるだけで頻度調整が効く:
        if not variant_bandit.should_post_now("tumblr"): return
    係数1.0以上は常にTrue（枠を増やすのはスケジュール側の仕事）。0なら常にFalse。"""
    f = cadence_factor(platform)
    if f >= 1.0:
        return True
    ok = False if f <= 0.0 else _rand_ok(rng, f)
    if not ok:
        # 無言でスキップすると「タスク緑・投稿ゼロ」になり監視が誤検知する（既知事象）。
        # 必ず理由を残す。
        log_post(platform, {"skipped": "cadence", "factor": round(f, 3), "posted": False})
    return ok


def _rand_ok(rng, f):
    try:
        return rng.random() < f
    except Exception:
        return True


def with_utm_content(url: str, variant_key: str) -> str:
    """URLへ utm_content=<variant_key> を付与（既にある場合は触らない）。"""
    if not url or not variant_key or "utm_content=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_content={variant_key}"


def log_post(platform: str, record: dict) -> None:
    """posted_log.json へ追記。ログ失敗で投稿処理は絶対に止めない。"""
    try:
        data = {"posts": []}
        if LOG_PATH.exists():
            loaded = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = {"posts": loaded}
            elif isinstance(loaded, dict):
                data = loaded
        rec = dict(record or {})
        # Only the actual provider-confirmed record can carry the selected arm.
        pub = rec.get('publication_receipt')
        selection = _TRIAL_SELECTION.pop(platform + '.title', None)
        if (platform == 'deviantart' and isinstance(pub,dict) and selection and
                pub.get('verified') is True and pub.get('operation') == 'create' and
                pub.get('provider_stage') == 'publish' and pub.get('post_id') and
                pub.get('verification_scope') in {'provider_create_response','ui_create_confirmation'} and
                pub.get('content_hashes',{}).get('title') == selection['title_sha256'] and
                rec.get('variants',{}).get('deviantart.title') == selection['variant_id'] and
                hashlib.sha256(pub.get('content',{}).get('title','').encode()).hexdigest() == selection['title_sha256']):
            pub = dict(pub, trial_assignment=dict(selection))
            if selection.get('runtime_evidence'):
                pub['runtime_evidence']=dict(selection['runtime_evidence'])
            rec['publication_receipt'] = pub
        rec.setdefault("platform", platform)
        rec.setdefault("posted_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        posts = data.setdefault("posts", [])
        posts.append(rec)
        if len(posts) > MAX_LOG_POSTS:
            data["posts"] = posts[-MAX_LOG_POSTS:]
        LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[variant_bandit] log_post skipped: {e}")
