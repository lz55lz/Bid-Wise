import { ref } from 'vue'

// 仅在本次 SPA 跳转中临时保留文件；不会写入浏览器存储。
const pendingTenderFile = ref<File | null>(null)
const pendingLegalFile = ref<File | null>(null)

export function usePendingTenderUpload() {
  const setPendingTenderFile = (file: File) => {
    pendingTenderFile.value = file
  }

  const consumePendingTenderFile = () => {
    const file = pendingTenderFile.value
    pendingTenderFile.value = null
    return file
  }

  const setPendingLegalFile = (file: File) => {
    pendingLegalFile.value = file
  }

  const consumePendingLegalFile = () => {
    const file = pendingLegalFile.value
    pendingLegalFile.value = null
    return file
  }

  return {
    pendingTenderFile,
    pendingLegalFile,
    setPendingTenderFile,
    consumePendingTenderFile,
    setPendingLegalFile,
    consumePendingLegalFile,
  }
}
