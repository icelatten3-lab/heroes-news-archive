# 키움 히어로즈 뉴스 아카이브

구글 뉴스 RSS에서 키워드 기사를 **3시간마다 자동 수집**해 웹페이지로 보여주는 아카이브.
팀원은 링크로 보기만 하고, 편집자는 같은 페이지에서 메모·태그·중요 표시를 남김.

## 구성
| 파일 | 역할 |
|---|---|
| `.github/workflows/fetch.yml` | 3시간 간격 자동 실행 (한국시간 00:10, 03:10 … 21:10) + 수동 실행 |
| `scripts/fetch.py` | 구글 뉴스 RSS 조회 → 중복 제거 → `docs/data/YYYY-MM.json` 에 누적 (기존 기사는 지우지 않음) |
| `docs/config.json` | 키워드·제외어·기본 태그 (여기만 고치면 됨) |
| `docs/index.html` | 웹 화면 (GitHub Pages) |
| `docs/annotations.json` | 메모·태그·중요·숨김 기록 (편집 모드에서 자동 저장) |

## 처음 설치 (약 10분)
1. **저장소 만들기**
   - GitHub 로그인 → 오른쪽 위 `+` → New repository
   - 이름 예: `kiwoom-news-archive`, **Public** 선택 (무료 Pages는 Public만 가능) → Create
2. **파일 올리기**
   - 저장소 화면 `uploading an existing file` 클릭 → 이 폴더 안의 `.github`, `docs`, `scripts`, `README.md` 를 통째로 끌어다 놓기 → Commit changes
   - `.github` 폴더가 빠졌다면: `Add file → Create new file` → 파일명에 `.github/workflows/fetch.yml` 입력 → 내용 붙여넣기
3. **웹페이지 켜기**
   - Settings → Pages → Branch: `main`, 폴더: `/docs` → Save
   - 1~2분 뒤 주소 생성: `https://<아이디>.github.io/kiwoom-news-archive/`
4. **첫 수집 (과거 기사 소급)**
   - Actions 탭 → 왼쪽 `뉴스 수집` → `Run workflow` → `backfill_days` 에 `30` 입력 → 실행
   - 2~3분 뒤 완료되면 웹페이지에 기사 표시. 이후엔 3시간마다 자동

## 편집 모드 (메모·태그 남기기)
편집 권한은 **토큰을 가진 사람만**. 팀원 화면엔 편집 버튼만 보이고 저장은 불가.
1. GitHub → 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
   - Repository access: `Only select repositories` → 이 저장소만 선택
   - Permissions → Repository permissions → **Contents: Read and write**
   - Expiration: 원하는 기간 (최대 1년, 만료되면 새로 발급)
2. 웹페이지 → `편집 모드` → 토큰 붙여넣기 + 표시할 이름 입력
   - 토큰은 그 브라우저에만 저장됨. 다른 PC에선 다시 입력
3. 기사 카드의 `☆`(중요) · `메모·태그` · `숨김`(관련 없는 기사 감추기) 사용
   - 저장 즉시 내 화면 반영, 팀원 화면은 1~2분 뒤 반영

## 키워드·태그 바꾸기
GitHub에서 `docs/config.json` 열기 → 연필 아이콘 → 수정 → Commit
- `queries`: 검색 키워드 (여러 개 가능)
- `exclude`: 제목에 들어가면 제외할 단어 (예: 키움증권)
- `tags`: 기본 태그 목록 (편집 중 새 태그를 바로 만들어도 됨)
- `window`: 한 번에 조회할 기간. 3시간 간격 수집에 6h면 겹쳐서 누락 없음. 기사가 폭증해 로그에 "100건 상한" 경고가 뜨면 `4h` 로 줄이기

## 권한 넘기기
- **편집자 추가**: Settings → Collaborators → 상대 GitHub 계정 초대 → 상대가 본인 토큰 발급 후 편집 모드 사용 (주소 그대로)
- **소유권 이전**: Settings → 맨 아래 Transfer ownership → 웹 주소가 `<새아이디>.github.io/...` 로 바뀜

## 참고·주의
- **공개 범위**: Public 저장소 + Pages라 주소를 아는 사람은 누구나 볼 수 있음 (검색엔진 노출은 차단 설정). 메모에 민감한 내부 정보는 적지 않기
- 기사 링크는 구글 뉴스 경유 주소 → 클릭하면 언론사 원문으로 이동
- 구글 뉴스 기반이라 네이버에만 노출되는 소규모 매체 기사는 빠질 수 있음 → 필요 시 네이버 검색 API 추가 가능
- GitHub 예약 실행은 몇 분~수십 분 늦게 돌 수 있음 (조회 기간이 겹쳐 누락은 없음)
- 수집이 멈춘 것 같으면 Actions 탭에서 실패 기록 확인 → `Run workflow` 로 수동 실행
