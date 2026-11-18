const API=(window.ENGAZ_API_URL||localStorage.getItem("engazApiUrl")||"http://127.0.0.1:8000").replace(/\/$/,"");
const isAdmin=document.title.includes("الإدارة");
const key=isAdmin?"engazAdminToken":"engazStudentToken";
function token(){return sessionStorage.getItem(key)}
async function api(path,options={}){
 const r=await fetch(API+path,{...options,headers:{"Content-Type":"application/json",...(token()?{"Authorization":"Bearer "+token()}:{}),...(options.headers||{})}});
 const data=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(data.detail||"تعذر الاتصال");
 return data;
}
function esc(x){return String(x).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
