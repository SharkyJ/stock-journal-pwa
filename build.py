#!/usr/bin/env python3
"""index.html을 수정한 뒤 실행하세요.

- 인라인 <script>의 SHA-256 해시로 CSP(script-src)를 다시 계산해 index.html에 반영
- sw.js 캐시 버전을 갱신(새 배포 시 앱 셸 캐시 교체)
- manifest.webmanifest 재생성

사용: python3 build.py          # 갱신
      python3 build.py --check  # 해시가 최신인지 검사만 (CI용, 불일치 시 exit 1)
"""
import base64, hashlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
INDEX = ROOT / "index.html"

html = INDEX.read_text(encoding="utf-8")
script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
version = re.search(r"const VERSION='([^']+)'", html).group(1)

csp = "; ".join([
    "default-src 'none'",
    f"script-src 'sha256-{digest}' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    "connect-src https://sheets.googleapis.com https://www.googleapis.com https://oauth2.googleapis.com https://accounts.google.com/gsi/ https://*.workers.dev",
    "frame-src https://accounts.google.com/gsi/",
    "img-src 'self' data:",
    "manifest-src 'self'",
    "worker-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
])
meta_re = re.compile(r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(">)')
current = meta_re.search(html)
if not current:
    sys.exit("index.html에 CSP meta 태그가 없습니다")

if "--check" in sys.argv:
    if current.group(2) != csp:
        sys.exit("CSP 해시가 index.html 스크립트와 다릅니다. python3 build.py 를 실행하세요.")
    print("CSP OK", "sha256-" + digest)
    sys.exit(0)

INDEX.write_text(meta_re.sub(lambda m: m.group(1) + csp + m.group(3), html, count=1), encoding="utf-8")

manifest = {
    "name": "주식일지", "short_name": "주식일지", "lang": "ko",
    "description": "국장·미장 보유종목, 매매기록, 자산추이 관리 (Google Sheets 연동)",
    "start_url": "./", "scope": "./", "display": "standalone", "orientation": "portrait",
    "background_color": "#0b0f17", "theme_color": "#0b0f17",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"},
    ],
}
(ROOT / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

sw = f"""// 앱 셸만 캐시합니다. Google API 응답(시트 데이터)은 절대 캐시하지 않습니다.
const V='sj-{version}-{digest[:8]}';
const SHELL=['./','./index.html','./manifest.webmanifest','./icon.svg','./icon-192.png','./icon-512.png'];
self.addEventListener('install',e=>{{e.waitUntil(caches.open(V).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()))}});
self.addEventListener('activate',e=>{{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))}});
self.addEventListener('fetch',e=>{{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.origin!==self.location.origin)return; // 외부 요청은 관여하지 않음
  if(e.request.mode==='navigate'){{
    e.respondWith(fetch(e.request).then(r=>{{const c=r.clone();caches.open(V).then(x=>x.put('./index.html',c));return r}}).catch(()=>caches.match('./index.html')));
    return;
  }}
  e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));
}});
"""
(ROOT / "sw.js").write_text(sw, encoding="utf-8")
print("built", version, "sha256-" + digest)
