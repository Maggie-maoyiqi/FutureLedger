export type PlanningSimulation = {
  points_cents: number[]
  min_balance_cents: number
  min_day: number
  feasible: boolean
  assumptions: Record<string, number>
  events: Record<string, string | number | boolean>[]
  conflicts: Record<string, string | number>[]
}
export type PlanningScenario = { name: string; result: PlanningSimulation }
