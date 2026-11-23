const API=(window.ENGAZ_API_URL||localStorage.getItem("engazApiUrl")||"http://127.0.0.1:8000").replace(/\/$/,"");
const isAdmin=document.title.includes("الإدارة");
const key=isAdmin?"engazAdminToken":"engazStudentToken";
function token(){return localStorage.getItem(key)||sessionStorage.getItem(key)}
async function api(path,options={}){
 const r=await fetch(API+path,{...options,headers:{"Content-Type":"application/json",...(token()?{"Authorization":"Bearer "+token()}:{}),...(options.headers||{})}});
 const data=await r.json().catch(()=>({}));
 const fieldLabels={username:"اسم المستخدم",phone:"رقم الهاتف السوري",password:"كلمة المرور",email:"البريد الإلكتروني",full_name:"الاسم الكامل",grade:"الصف الدراسي",governorate:"المحافظة",birth_year:"سنة الميلاد",gender:"الجنس",school:"المدرسة",code:"رمز التحقق"};
 if(!r.ok){
  let message="تعذر الاتصال";
  if(typeof data.detail==="string")message=data.detail;
  else if(Array.isArray(data.detail)){
   const fields=[...new Set(data.detail.map(x=>Array.isArray(x.loc)?x.loc[x.loc.length-1]:null).filter(Boolean))];
   const names=fields.map(x=>fieldLabels[x]||"أحد الحقول");
   message=names.length?"راجع البيانات التالية: "+names.join("، "):"تحقق من بيانات النموذج وأعد المحاولة";
  }else if(typeof data.message==="string")message=data.message;
  const e=new Error(message);e.status=r.status;throw e;
 }
 return data;
}
function esc(x){return String(x).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
