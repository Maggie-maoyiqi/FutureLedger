<script setup lang="ts">
import { computed } from 'vue'
import type { PlanningSimulation, PlanningScenario } from '../types/planning'
const props = defineProps<{ result?: PlanningSimulation; scenarios?: PlanningScenario[]; assumptions?: string[] }>()
const entries = computed(() => [...(props.result ? [{ name: '当前方案', result: props.result }] : []), ...(props.scenarios || [])])
const bounds = computed(() => {
  const values = entries.value.flatMap(entry => entry.result.points_cents)
  return { min: Math.min(0, ...values), max: Math.max(1, ...values) }
})
function money(value: number) { return `¥${(value / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` }
function curve(result: PlanningSimulation) {
  return result.points_cents.map((value, index, points) => `${index ? 'L' : 'M'} ${40 + index * 540 / Math.max(1, points.length - 1)} ${20 + (bounds.value.max - value) * 160 / (bounds.value.max - bounds.value.min)}`).join(' ')
}
const colors = ['#087d68', '#d57c4b', '#5877ba', '#a15d9a']
const assumptionLabels: Record<string, string> = { opening_balance_cents: '期初余额', floor_cents: '最低保留余额', goal_cents: '目标支出', daily_budget_cents: '每日预算', days: '模拟天数', salary_day: '发薪日', purchase_day: '购买日' }
const eventLabels: Record<string, string> = { rent: '房租', cloud_storage: '云存储', video_subscription: '视频订阅', salary: '工资', computer_goal: '电脑购买' }
</script>
<template>
  <section class="result-card planning-result">
    <div class="visual-title">现金流模拟</div>
    <ul v-if="assumptions?.length"><li v-for="item in assumptions" :key="item">{{ item }}</li></ul>
    <svg v-if="entries.length" viewBox="0 0 640 225" class="result-chart" role="img" aria-label="各方案每日余额曲线">
      <text x="0" y="14" font-size="11">{{ money(bounds.max) }}</text><text x="0" y="198" font-size="11">{{ money(bounds.min) }}</text>
      <line x1="40" :y1="20 + bounds.max * 160 / (bounds.max - bounds.min)" x2="580" :y2="20 + bounds.max * 160 / (bounds.max - bounds.min)" stroke="#ccc"/>
      <path v-for="(entry, index) in entries" :key="entry.name" :d="curve(entry.result)" fill="none" :stroke="colors[index % colors.length]" stroke-width="3"/>
      <text x="40" y="220" font-size="11">第1天</text><text x="540" y="220" font-size="11">模拟结束</text>
    </svg>
    <article v-for="(entry, index) in entries" :key="entry.name">
      <h4 :style="{ color: colors[index % colors.length] }">{{ entry.name }} · {{ entry.result.feasible ? '可行' : '存在余额风险' }}</h4>
      <p>最低余额 {{ money(entry.result.min_balance_cents) }}，出现在第{{ entry.result.min_day }}天</p>
      <ul><li v-for="(value, key) in entry.result.assumptions" :key="key">{{ assumptionLabels[key] || key }}：{{ String(key).endsWith('_cents') ? money(value) : value }}</li></ul>
      <p v-for="(conflict, conflictIndex) in entry.result.conflicts" :key="conflictIndex">第{{ conflict.day }}天余额低于最低保留余额{{ typeof conflict.floor_cents === 'number' ? ` ${money(conflict.floor_cents)}` : '' }}</p>
      <details v-if="entry.result.events.length"><summary>模拟收支事件</summary><ul><li v-for="(event, eventIndex) in entry.result.events" :key="eventIndex">第{{ event.day }}天 · {{ eventLabels[String(event.source)] || event.source }} · {{ typeof event.signed_cents === 'number' ? money(event.signed_cents) : '—' }}</li></ul></details>
    </article>
  </section>
</template>
