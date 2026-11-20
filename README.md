# 포트폴리오 매니저

여러 포트폴리오에 주식·ETF·금·채권을 한국/미국 시장별로 기록하고, 지연 시세로 평가금액·손익·배당·분석을 한눈에 보는 Windows 프로그램이에요. 인터넷 없이도 입력한 내용은 내 PC에만 저장돼요.

## 설치 (Windows 10/11, 64비트)

**방법 1. 설치 파일**: `PortfolioManager_Setup.exe`를 내려받아 실행해요.

**방법 2. 압축 파일(설치 없이)**: `PortfolioManager_portable.zip`을 풀고 `PortfolioManager.exe`를 실행해요. 폴더를 통째로 옮기면 어디서든 쓸 수 있어요.

파이썬은 설치하지 않아도 돼요.

### 처음 실행할 때 경고가 뜨면
- 브라우저 다운로드 경고: '유지'를 눌러요.
- "Windows의 PC 보호" 화면: **추가 정보 → 실행**을 눌러요. 개인이 만든 무료 프로그램이라 서명이 없어서 뜨는 안내예요.
- 백신이 막으면: 파일을 예외로 허용하거나 압축 파일 방식으로 받아 보세요.
- `VCRUNTIME140…dll` 오류가 나면 Microsoft의 'Visual C++ 재배포 가능 패키지(x64)'를 설치해요.

## 데이터 위치
- 설치 폴더 안의 `DB\portfolio.db` (쓰기가 안 되는 위치면 `%LOCALAPPDATA%\PortfolioManager`)
- 업데이트하거나 지워도 `DB` 폴더는 남아요. 백업하려면 이 폴더를 복사하세요.
- 오류가 나면 같은 폴더에 `error.log`가 생겨요.

## 꼭 알아 두세요
- 투자 권유가 아니며 참고용이에요. 시세·재무·배당 정보는 야후 파이낸스와 네이버 증권의 **비공식 주소**에서 가져와서 지연되거나 틀리거나 갑자기 안 나올 수 있어요. 투자 판단과 책임은 본인에게 있어요.
- 배당 일정은 과거 12개월 기록을 1년 뒤로 옮긴 예상치예요.

## 라이선스 / 사용한 오픈소스
- 화면은 PySide6(Qt, LGPL)로 만들었어요.

## 개발자용
```
pip install -r requirements.txt
python stock_app.py
```
설치 파일 만들기: `build_exe.bat` 실행 후 Inno Setup으로 `PortfolioManager_setup.iss`를 컴파일해요.
(깃허브에 올리고 `v1.0.0` 같은 태그를 푸시하면 `.github/workflows/build.yml`이 자동으로 빌드해서 릴리스에 올려요.)
