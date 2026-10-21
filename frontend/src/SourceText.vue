<script setup lang="ts">
import { computed } from 'vue';
import type { Paragraph } from './api';
const props=defineProps<{ paragraph:Paragraph; documentId:string; text?:string }>();
const fragments=computed(()=>{
  const maths=new Map((props.paragraph.content?.parts || []).filter((p:any)=>p.type==='math').map((p:any)=>[p.token,p]));
  return (props.text ?? props.paragraph.text).split(/(PPMATH\d{6})/g).filter(Boolean).map((text:string)=>({text,math:maths.get(text) as any}));
});
</script>
<template><template v-for="(part,index) in fragments" :key="index"><img v-if="part.math" class="inline-formula" :src="`/api/documents/${documentId}/assets/${part.math.asset}`" :style="{width:`${part.math.width / (part.math.size || 10)}em`,height:`${part.math.height / (part.math.size || 10)}em`}" alt="原稿公式" loading="lazy" decoding="async" /><span v-else>{{ /^PPMATH\d{6}$/.test(part.text) ? '[原稿公式]' : part.text }}</span></template></template>
