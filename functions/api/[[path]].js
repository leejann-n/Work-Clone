// Cloudflare Pages Function: 화면에서 부르는 /api/* 요청을 Render 백엔드로 그대로 전달합니다.
// Cloudflare Pages → Settings → Variables and Secrets 에 BACKEND_URL 을 설정하세요.
//   예) BACKEND_URL = https://workclone-api.onrender.com
export async function onRequest(context) {
  const backend = (context.env.BACKEND_URL || '').replace(/\/$/, '');
  if (!backend) {
    return new Response(JSON.stringify({ detail: 'Cloudflare Pages에 BACKEND_URL 환경 변수를 설정하세요.' }), {
      status: 500,
      headers: { 'content-type': 'application/json; charset=utf-8' },
    });
  }
  const url = new URL(context.request.url);
  const target = backend + url.pathname + url.search;
  // 요청(메서드·헤더·본문)을 그대로 넘기고, 응답(AI 진행 상황 스트림 포함)을 그대로 돌려줌
  return fetch(new Request(target, context.request));
}
