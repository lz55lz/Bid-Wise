import dayjs from 'dayjs'

export const formatDate = (date: string | Date | null | undefined, format = 'YYYY-MM-DD HH:mm'): string => {
  if (!date) return '-'
  return dayjs(date).format(format)
}

export const formatDateShort = (date: string | Date | null | undefined): string => {
  return formatDate(date, 'MM/DD')
}

export const formatAmount = (amount: number | null | undefined, currency = '¥'): string => {
  if (amount == null) return '-'
  return `${currency}${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export const isOverdue = (deadline: string | Date | null | undefined): boolean => {
  if (!deadline) return false
  return dayjs(deadline).isBefore(dayjs())
}

export const getRelativeTime = (date: string | Date): string => {
  const now = dayjs()
  const target = dayjs(date)
  const diffMinutes = now.diff(target, 'minute')
  const diffHours = now.diff(target, 'hour')
  const diffDays = now.diff(target, 'day')

  if (diffMinutes < 1) return '刚刚'
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`
  if (diffHours < 24) return `${diffHours} 小时前`
  if (diffDays < 7) return `${diffDays} 天前`
  return formatDate(date, 'YYYY-MM-DD')
}
