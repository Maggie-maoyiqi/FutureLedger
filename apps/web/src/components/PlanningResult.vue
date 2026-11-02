<script setup lang="ts">
import { computed } from 'vue'
import type { PlanningSimulation, PlanningScenario } from '../types/planning'

const props = defineProps<{
  result?: PlanningSimulation
  earliestPurchaseDay?: number | null
  scenarios?: PlanningScenario[]
  assumptions?: string[]
}>()

function money(value: number | undefined) {
  if (typeof value !== 'number') return '—'
  return `¥${(value / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const resultAssumptions = computed(() => props.result?.assumptions || {})
const projectionOnly = computed(() => props.result?.assumptions.goal_cents === 0)
const recommendation = computed(() => {
  if (!props.result) return '暂时无法生成购买建议。'
  if (projectionOnly.value) return props.result.feasible ? '当前现金流预测满足最低保留余额。' : '当前现金流预测存在余额风险。'
  if (props.earliestPurchaseDay == null) return '在当前模拟期限内，没有满足最低保留余额的购买日期。'
  return `建议在第${props.earliestPurchaseDay}天或之后购买。`
})
const reason = computed(() => {
  if (!props.result) return ''
  if (projectionOnly.value) return `模拟最低余额为${money(props.result.min_balance_cents)}，出现在第${props.result.min_day}天；最低保留余额为${money(resultAssumptions.value.floor_cents)}。`
  if (props.earliestPurchaseDay == null) return '如果现在购买，预计余额会跌破你设定的最低保留余额。'
  return `这样可以避开发薪和固定支出造成的资金低谷；模拟最低余额为${money(props.result.min_balance_cents)}，高于最低保留余额${money(resultAssumptions.value.floor_cents)}。`
})
const entries = computed(() => [...(props.result ? [{ name: '当前方案', result: props.result }] : []), ...(props.scenarios || [])])
const bounds = computed(() => {
  const values = entries.value.flatMap(entry => entry.result.points_cents)
  return { min: Math.min(0, ...values), max: Math.max(1, ...values) }
})
function curve(result: PlanningSimulation) {
  return result.points_cents.map((value, index, points) => `${index ? 'L' : 'M'} ${40 + index * 540 / Math.max(1, points.length - 1)} ${20 + (bounds.value.max - value) * 160 / (bounds.value.max - bounds.value.min)}`).join(' ')
}
const colors = ['#087d68', '#d57c4b', '#5877ba', '#a15d9a']
const assumptionLabels: Record<string, string> = { opening_balance_cents: '期初余额', floor_cents: '最低保留余额', goal_cents: '目标支出', daily_budget_cents: '每日预算', days: '模拟天数', salary_day: '发薪日', purchase_day: '购买日' }
const eventLabels: Record<string, string> = { rent: '房租', cloud_storage: '云存储', video_subscription: '视频订阅', salary: '工资', computer_goal: '电脑购买' }
</script>

<template>
  <section v-if="result" class="result-card planning-result">
    <div class="visual-title">{{ projectionOnly ? '现金流预测' : '购买建议' }}</div>
    <h3>{{ recommendation }}</h3>
    <p>{{ reason }}</p>
    <ul class="planning-assumptions">
      <li>期初余额：{{ money(resultAssumptions.opening_balance_cents) }}</li>
      <li>最低保留余额：{{ money(resultAssumptions.floor_cents) }}</li>
      <li>目标支出：{{ money(resultAssumptions.goal_cents) }}</li>
      <li>每日预算：{{ money(resultAssumptions.daily_budget_cents) }}</li>
      <li>模拟天数：{{ resultAssumptions.days ?? '—' }}</li>
    </ul>
    <details><summary>查看模拟依据与方案比较</summary>
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
    </details>
  </section>
</template>
