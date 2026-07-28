import './async-D55cHugf.js';
import { c as spread_props } from './index-6p4UEISu.js';
import { H as Hs } from './2-DoIM9QLI.js';
import { k } from './BlockLabel-Cwr2q1Ma.js';
import { t as tick } from './index-server-CQz6EZl_.js';
import { p } from './Empty-cEfRNAPl.js';
import { i } from './File-DQh5d1OO.js';
import { y } from './IconButtonWrapper-DtthXzCF.js';
import { S, p as pl } from './FileUpload-C3CPqNpq.js';
import { G } from './Block-DFkF8ric.js';
import { k as k$1 } from './UploadText-BslqYKOD.js';
import { $ } from './index3-29FCdJla.js';
export { default as BaseExample } from './Example5-Sv6lxPPF.js';
import './escaping-CBnpiEl5.js';
import './context-CBkBucIx.js';
import './index5-BoOEKc6P.js';
import './dev-fallback-Bc5Ork7Y.js';
import './index-Cg-Pg6j3.js';
import './Upload2-B2r_dXYb.js';
import './IconButton-DoTLxBZ_.js';
import './Clear-D7Yjckqz.js';
import './Upload-BbxeBrrD.js';
import './DownloadLink-eCzvV1uC.js';
import './html-CfyvkLET.js';

function z(d,c){d.component(t=>{let{value:l,label:_,show_label:p$1,selectable:o,i18n:r,height:h,buttons:s=null,on_custom_button_click:i$1=null,on_select:n,on_download:m}=c;p$1&&s&&s.length>0?(t.push("<!--[-->"),y(t,{buttons:s,on_custom_button_click:i$1})):t.push("<!--[!-->"),t.push("<!--]--> "),k(t,{show_label:p$1,float:l===null,Icon:i,label:_||"File"}),t.push("<!----> "),l&&(!Array.isArray(l)||l.length>0)?(t.push("<!--[-->"),S(t,{i18n:r,selectable:o,value:l,height:h})):(t.push("<!--[!-->"),p(t,{unpadded_box:true,size:"large",children:u=>{i(u);},$$slots:{default:true}})),t.push("<!--]-->");});}function M(d,c){d.component(t=>{const{$$slots:l,$$events:_,...p}=c;let o=null,r=false;class h extends Hs{async get_data(){return o&&(await o,await tick()),await super.get_data()}}const s=new h(p);s.props.value;let i=true,n;function m(u){G(u,{visible:s.shared.visible,variant:s.props.value?"solid":"dashed",border_mode:r?"focus":"base",padding:false,elem_id:s.shared.elem_id,elem_classes:s.shared.elem_classes,container:s.shared.container,scale:s.shared.scale,min_width:s.shared.min_width,allow_overflow:false,children:e=>{$(e,spread_props([{autoscroll:s.shared.autoscroll,i18n:s.i18n},s.shared.loading_status,{status:s.shared.loading_status?.status||"complete",on_clear_status:()=>s.dispatch("clear_status",s.shared.loading_status)}])),e.push("<!----> "),s.shared.interactive?(e.push("<!--[!-->"),pl(e,{upload:(...a)=>s.shared.client.upload(...a),stream_handler:(...a)=>s.shared.client.stream(...a),label:s.shared.label,show_label:s.shared.show_label,value:s.props.value,file_count:s.props.file_count,file_types:s.props.file_types,selectable:s.props._selectable,height:s.props.height??void 0,root:s.shared.root,allow_reordering:s.props.allow_reordering,max_file_size:s.shared.max_file_size,buttons:s.props.buttons,on_custom_button_click:a=>{s.dispatch("custom_button_click",{id:a});},onchange:a=>{s.props.value=a;},ondrag:a=>r=a,onclear:()=>s.dispatch("clear"),onselect:a=>s.dispatch("select",a),onupload:()=>s.dispatch("upload"),onerror:a=>{s.shared.loading_status=s.shared.loading_status||{},s.shared.loading_status.status="error",s.dispatch("error",a);},ondelete:a=>{s.dispatch("delete",a);},i18n:s.i18n,get upload_promise(){return o},set upload_promise(a){o=a,i=false;},children:a=>{k$1(a,{i18n:s.i18n,type:"file"});},$$slots:{default:true}})):(e.push("<!--[-->"),z(e,{on_select:({detail:a})=>s.dispatch("select",a),on_download:({detail:a})=>s.dispatch("download",a),selectable:s.props._selectable,value:s.props.value,label:s.shared.label,show_label:s.shared.show_label,height:s.props.height,i18n:s.i18n,buttons:s.props.buttons,on_custom_button_click:a=>{s.dispatch("custom_button_click",{id:a});}})),e.push("<!--]-->");},$$slots:{default:true}});}do i=true,n=t.copy(),m(n);while(!i);t.subsume(n);});}

export { z as BaseFile, pl as BaseFileUpload, S as FilePreview, M as default };
//# sourceMappingURL=Index22-vO09wCm2.js.map
