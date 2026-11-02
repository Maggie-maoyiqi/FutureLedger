<script setup lang="ts">
import { ref } from 'vue'
import { useWorkspace } from '../stores/workspace'
import ResultCard from '../components/ResultCard.vue'
import PlanningResult from '../components/PlanningResult.vue'
import TransferCard from '../components/TransferCard.vue'

const workspace = useWorkspace(); const draft = ref('')
const suggestions = ['这个月的支出是多少？', '8月和9月的各项净支出', '上个月的收入明细', '我想存6000元买电脑，至少留3000元']
async function send(value = draft.value) { const text = value.trim(); if (!text) return; draft.value = ''; await workspace.send(text).catch(() => undefined) }
</script>
<template>
  <div class="assistant-layout">
    <section class="card chat-card">
      <div class="chat-head"><div><span class="eyebrow">FUTURELEDGER AI</span><h1>智能体小智</h1></div><span class="mode-pill api"><i></i>LLM API</span></div>
      <div class="chat-body">
        <div v-if="!workspace.messages.length" class="welcome-message"><div class="assistant-avatar">✦</div><h2>你好，我是你的财务助手</h2><p>查询账单、制定存钱计划，或发起需要确认的演示转账。</p><div class="suggestions"><button v-for="question in suggestions" :key="question" @click="send(question)">{{ question }}　↗</button></div></div>
        <div v-for="(message, index) in workspace.messages" :key="index" class="message" :class="message.role">
          <div v-if="message.role === 'assistant'" class="assistant-avatar small">✦</div>
          <div>
            <p>{{ message.text }}</p>
            <ResultCard v-if="message.role === 'assistant' && (message.rows?.length || message.chart === 'metric_card')" :result="message" />
            <a v-if="message.role === 'assistant' && message.query_id" class="text-link" :href="`/api/queries/${message.query_id}/export.csv?session_id=${encodeURIComponent(workspace.sessionId)}`" target="_blank">导出 CSV</a>
            <PlanningResult v-if="message.status === 'simulated'" :result="message.result" :scenarios="message.scenarios" :assumptions="message.assumptions" />
            <TransferCard v-if="message.status === 'confirmation_required' && message.action_id" :key="message.action_id" :action-id="message.action_id" />
          </div>
        </div>
        <div v-if="workspace.loading" class="loading-line">正在理解问题并查询授权范围内的数据…</div>
      </div>
      <form class="composer" @submit.prevent="send()"><textarea v-model="draft" rows="2" placeholder="输入你的问题…" @keydown.enter.exact.prevent="send()"></textarea><div class="composer-foot"><span>查询结果不展示 SQL、IntentPlan 或内部流水 ID</span><button class="send-button" :disabled="!draft.trim() || workspace.loading">发送　↑</button></div></form>
    </section>
    <aside class="card assistant-aside"><h3>快捷场景</h3><button @click="draft='2026年9月各类别净支出排名前五'">▤　分析本月支出</button><button @click="draft='最近三个月每月餐饮净支出趋势'">⌁　查看支出趋势</button><button @click="draft='给测试用户B转25元'">↻　演示转账</button><div class="aside-note"><span>ⓘ</span><p>LLM 只负责理解和生成结构化计划，查询、权限和转账确认由后端完成。</p></div><div class="service-status"><i></i><span>当前用户：{{ workspace.username }}<small>仅使用合成数据</small></span></div></aside>
  </div>
</template>
