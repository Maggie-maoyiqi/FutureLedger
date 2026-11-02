<script setup lang="ts">
import { computed } from 'vue'
import type { PlanningSimulation } from '../types/planning'

const props = defineProps<{
  result?: PlanningSimulation
  earliestPurchaseDay?: number | null
}>()

function money(value: number | undefined) {
  if (typeof value !== 'number') return '—'
  return `¥${(value / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const assumptions = computed(() => props.result?.assumptions || {})
const recommendation = computed(() => {
  if (!props.result) return '暂时无法生成购买建议。'
  if (props.earliestPurchaseDay == null) return '在当前模拟期限内，没有满足最低保留余额的购买日期。'
  return `建议在第${props.earliestPurchaseDay}天或之后购买。`
})
const reason = computed(() => {
  if (!props.result) return ''
  if (props.earliestPurchaseDay == null) return '如果现在购买，预计余额会跌破你设定的最低保留余额。'
  return `这样可以避开发薪和固定支出造成的资金低谷；模拟最低余额为${money(props.result.min_balance_cents)}，高于最低保留余额${money(assumptions.value.floor_cents)}。`
})
</script>

<template>
  <section v-if="result" class="result-card planning-result">
    <div class="visual-title">购买建议</div>
    <h3>{{ recommendation }}</h3>
    <p>{{ reason }}</p>
    <ul class="planning-assumptions">
      <li>期初余额：{{ money(assumptions.opening_balance_cents) }}</li>
      <li>最低保留余额：{{ money(assumptions.floor_cents) }}</li>
      <li>目标支出：{{ money(assumptions.goal_cents) }}</li>
      <li>每日预算：{{ money(assumptions.daily_budget_cents) }}</li>
      <li>模拟天数：{{ assumptions.days ?? '—' }}</li>
    </ul>
  </section>
</template>
