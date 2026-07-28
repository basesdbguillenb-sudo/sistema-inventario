import{S as r}from"./index-Bozcp1K3.js";import"./index-DyDV80mF.js";const a="shadowMapFragmentSoftTransparentShadow",o=`#if SM_SOFTTRANSPARENTSHADOW==1
if ((bayerDither8(floor(((fragmentInputs.position.xy)%(8.0)))))/64.0>=uniforms.softTransparentShadowSM.x*alpha) {discard;}
#endif
`;r.IncludesShadersStoreWGSL[a]||(r.IncludesShadersStoreWGSL[a]=o);const S={name:a,shader:o};export{S as shadowMapFragmentSoftTransparentShadowWGSL};
