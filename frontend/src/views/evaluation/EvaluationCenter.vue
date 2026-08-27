<template>
  <div class="page">
    <header>
      <div>
        <small>QUALITY EVALUATION</small>
        <h1>评测中心</h1>
        <p>默认只验证法律知识；选择项目后再同时验证项目文件检索。</p>
      </div>
      <div class="header-actions">
        <el-button @click="openCreate">新建题集</el-button>
        <el-button type="primary" :icon="VideoPlay" :loading="running" @click="run">
          运行评测
        </el-button>
      </div>
    </header>

    <section class="panel controls">
      <div>
        <h2>选择评测题集</h2>
        <p>内置基准集未选项目时只运行法律题；自定义题集用于补充你的业务问题。</p>
      </div>
      <el-select v-model="selectedSetId" clearable placeholder="内置基准集" class="set-select">
        <el-option label="内置基准集（只读）" value="" />
        <el-option
          v-for="item in enabledSets"
          :key="item.id"
          :label="`${item.name} · v${item.version} · ${item.cases.length} 题`"
          :value="item.id"
        />
      </el-select>
      <el-select v-model="selectedProjectId" clearable placeholder="关联项目（仅项目文件题需要）" class="set-select">
        <el-option v-for="item in projects" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
    </section>

    <el-alert type="info" :closable="false" show-icon>
      不生成答案：每题只检查前 5 条检索结果中是否包含期望 Evidence。展开结果行可查看命中的原文。
    </el-alert>

    <section class="panel">
      <div class="head">
        <div><h2>我的题集</h2><p>题集可编辑、启停和删除；每次编辑都会形成新版本号。</p></div>
        <el-button text :loading="loadingSets" @click="loadSets">刷新</el-button>
      </div>
      <el-empty v-if="!loadingSets && sets.length === 0" description="还没有自定义题集" :image-size="68" />
      <el-table v-else :data="sets" stripe>
        <el-table-column prop="name" label="题集" min-width="180" />
        <el-table-column label="题数" width="90"><template #default="{ row }">{{ row.cases.length }}</template></el-table-column>
        <el-table-column label="版本" width="90"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row as EvaluationSet)">编辑</el-button>
            <el-button link @click="toggleEnabled(row as EvaluationSet)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-popconfirm title="删除后不可恢复，确认删除？" @confirm="removeSet(row as EvaluationSet)"><template #reference><el-button link type="danger">删除</el-button></template></el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="result" class="panel">
      <div class="head">
        <div><h2>本次真实结果</h2><p>通过代表前 5 条检索结果中出现了预期原文，不代表最终回答已通过人工审查。</p></div>
        <el-tag :type="result.passed === result.total ? 'success' : 'warning'">{{ result.passed }}/{{ result.total }} 题命中</el-tag>
      </div>
      <div class="stats"><div><b>{{ Math.round(result.recall_at_5 * 100) }}%</b><span>Recall@5</span></div><div><b>{{ result.elapsed_ms }} ms</b><span>总耗时</span></div><div><b>{{ result.total }}</b><span>已执行题数</span></div><div v-if="result.skipped"><b>{{ result.skipped }}</b><span>未执行（未选项目）</span></div></div>
      <el-table :data="result.results" stripe>
        <el-table-column type="expand"><template #default="{ row }"><div class="detail"><b>期望证据</b><p>{{ row.expected?.join('；') || '未配置' }}</p><b>实际命中原文</b><p>{{ row.matched_excerpt || '未在前 5 条结果中命中' }}</p></div></template></el-table-column>
        <el-table-column prop="question" label="测试问题" min-width="280" />
        <el-table-column prop="scope" label="范围" width="110" />
        <el-table-column label="结果" width="140"><template #default="{ row }"><el-tag :type="row.skipped ? 'info' : row.passed ? 'success' : 'danger'" size="small">{{ row.skipped ? '未执行' : row.passed ? `通过${row.rank ? ` · 第${row.rank}条` : ''}` : '未命中' }}</el-tag></template></el-table-column>
        <el-table-column label="说明" min-width="220"><template #default="{ row }">{{ row.error || (row.passed ? '前 5 条结果包含期望证据' : '前 5 条结果未找到期望证据') }}</template></el-table-column>
      </el-table>
    </section>

    <section v-else class="empty"><el-icon><DataAnalysis /></el-icon><h2>还没有评测结果</h2><p>选择题集后点击“运行评测”，查看真实检索表现。</p></section>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑题集' : '新建题集'" width="760px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="题集名称" required><el-input v-model.trim="form.name" maxlength="128" show-word-limit /></el-form-item>
        <el-form-item label="说明"><el-input v-model.trim="form.description" type="textarea" :rows="2" /></el-form-item>
        <div class="case-title"><b>测试问题</b><el-button text type="primary" @click="addCase">添加问题</el-button></div>
        <div v-for="(item, index) in form.cases" :key="index" class="case-card">
          <div class="case-head"><span>问题 {{ index + 1 }}</span><el-button link type="danger" :disabled="form.cases.length === 1" @click="removeCase(index)">移除</el-button></div>
          <el-input v-model.trim="item.question" placeholder="例如：投标保证金上限是多少？" />
          <div class="case-options"><el-select v-model="item.scope"><el-option label="法律知识库" value="knowledge" /><el-option label="项目文件" value="project" /></el-select><el-input v-model="item.evidenceText" placeholder="期望原文，多个片段请用换行分隔" /></div>
        </div>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveSet">保存题集</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { DataAnalysis, VideoPlay } from '@element-plus/icons-vue'
import { evaluationApi, projectApi, type EvaluationSet, type EvaluationSetPayload } from '@/api'
import type { Project } from '@/types'

type Result = Awaited<ReturnType<typeof evaluationApi.runRag>>
type EditableCase = { question: string; scope: 'knowledge' | 'project'; evidenceText: string }

const result = ref<Result | null>(null)
const sets = ref<EvaluationSet[]>([])
const selectedSetId = ref('')
const selectedProjectId = ref('')
const projects = ref<Project[]>([])
const running = ref(false)
const loadingSets = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<string | null>(null)
const form = reactive<{ name: string; description: string; cases: EditableCase[] }>({ name: '', description: '', cases: [] })
const enabledSets = computed(() => sets.value.filter(item => item.enabled))

const blankCase = (): EditableCase => ({ question: '', scope: 'knowledge', evidenceText: '' })
const resetForm = () => { form.name = ''; form.description = ''; form.cases = [blankCase()] }
const loadSets = async () => { loadingSets.value = true; try { sets.value = await evaluationApi.listSets() } catch (e: any) { ElMessage.error(e?.message || '加载题集失败') } finally { loadingSets.value = false } }
const openCreate = () => { editingId.value = null; resetForm(); dialogVisible.value = true }
const openEdit = (item: EvaluationSet) => { editingId.value = item.id; form.name = item.name; form.description = item.description || ''; form.cases = item.cases.map(caseItem => ({ question: caseItem.question, scope: caseItem.scope, evidenceText: caseItem.expected_evidence.join('\n') })); dialogVisible.value = true }
const addCase = () => form.cases.push(blankCase())
const removeCase = (index: number) => form.cases.splice(index, 1)
const saveSet = async () => { const payload: EvaluationSetPayload = { name: form.name, description: form.description || null, cases: form.cases.map(item => ({ question: item.question, scope: item.scope, expected_evidence: item.evidenceText.split('\n').map(value => value.trim()).filter(Boolean) })) }; if (!payload.name || payload.cases.some(item => !item.question || !item.expected_evidence.length)) { ElMessage.warning('请填写题集名称、测试问题和至少一条期望原文'); return }; saving.value = true; try { if (editingId.value) await evaluationApi.updateSet(editingId.value, payload); else await evaluationApi.createSet(payload); await loadSets(); dialogVisible.value = false; ElMessage.success('题集已保存') } catch (e: any) { ElMessage.error(e?.message || '保存失败') } finally { saving.value = false } }
const toggleEnabled = async (item: EvaluationSet) => { try { await evaluationApi.setEnabled(item.id, !item.enabled); if (!item.enabled) selectedSetId.value = item.id; if (item.enabled && selectedSetId.value === item.id) selectedSetId.value = ''; await loadSets() } catch (e: any) { ElMessage.error(e?.message || '更新状态失败') } }
const removeSet = async (item: EvaluationSet) => { try { await evaluationApi.deleteSet(item.id); if (selectedSetId.value === item.id) selectedSetId.value = ''; await loadSets(); ElMessage.success('题集已删除') } catch (e: any) { ElMessage.error(e?.message || '删除失败') } }
const run = async () => { running.value = true; try { result.value = await evaluationApi.runRag(selectedProjectId.value || undefined, selectedSetId.value || undefined); ElMessage.success(result.value.skipped ? `评测完成，${result.value.skipped} 道项目题未执行` : '评测完成') } catch (e: any) { ElMessage.error(e?.message || '评测失败，请确认管理员权限与知识库索引') } finally { running.value = false } }

onMounted(async () => { await loadSets(); try { projects.value = await projectApi.list() } catch { /* Project selection is optional for legal-only evaluation. */ } })
</script>

<style scoped>
.page{max-width:1180px;margin:auto;padding:12px 4px 40px}.page header{display:flex;justify-content:space-between;align-items:center;padding:28px 30px;border-radius:18px;background:linear-gradient(120deg,#17264e,#31559f);color:#fff}.page header h1{margin:3px 0 7px;font-size:30px}.page header p,small{margin:0;color:#dce8ff}.page header small{letter-spacing:1.4px}.header-actions{display:flex;gap:10px}.panel{margin-top:18px;padding:22px;background:#fff;border:1px solid #e7edf7;border-radius:16px}.panel h2{margin:0 0 8px;font-size:18px}.panel p{margin:0;color:#778399}.controls{display:flex;justify-content:space-between;align-items:center;gap:24px}.set-select{width:320px}.head{display:flex;justify-content:space-between;gap:16px}.stats{display:flex;gap:48px;margin:24px 0}.stats div{display:flex;flex-direction:column}.stats b{font-size:26px}.stats span{margin-top:4px;color:#778399;font-size:13px}.detail{padding:8px 28px;color:#44506a}.detail p{margin:6px 0 16px;white-space:pre-wrap;line-height:1.7}.empty{text-align:center;padding:78px;color:#778399}.empty .el-icon{font-size:46px;color:#8aa9e5}.empty h2{margin:14px 0 8px;color:#35415a}.case-title,.case-head,.case-options{display:flex;align-items:center;justify-content:space-between;gap:12px}.case-title{margin:8px 0}.case-card{padding:14px;margin-top:10px;border:1px solid #e7edf7;border-radius:10px;background:#fafcff}.case-head{margin-bottom:10px;color:#52617c}.case-options{margin-top:10px}.case-options .el-select{width:150px}.case-options .el-input{flex:1}@media(max-width:700px){.page header,.controls{align-items:flex-start;gap:14px;flex-direction:column}.stats{gap:20px}.set-select{width:100%}.case-options{align-items:stretch;flex-direction:column}.case-options .el-select{width:100%}}
</style>
