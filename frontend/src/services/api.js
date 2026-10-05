import axios from 'axios'

// API configuration â€” default /api uses Vite dev proxy (same-origin, avoids ad-blocker XHR blocks)
const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'
const API_TIMEOUT = parseInt(import.meta.env.VITE_API_TIMEOUT) || 30000
// AI requests: OpenAI can take up to ~180s (60s x 3 attempts); matches Gunicorn and nginx timeouts.
const LONG_REQUEST_TIMEOUT = 300000

// Create axios instance
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  withCredentials: true, // Enable cookies for Django session auth
  headers: {
    'Content-Type': 'application/json',
  },
})

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

const CSRF_SAFE_METHODS = ['get', 'head', 'options', 'trace']

// Request interceptor - Django session auth; unsafe methods must echo the csrftoken cookie
apiClient.interceptors.request.use(
  (config) => {
    const method = (config.method || 'get').toLowerCase()
    const csrfToken = getCookie('csrftoken')
    if (csrfToken && !CSRF_SAFE_METHODS.includes(method)) {
      config.headers['X-CSRFToken'] = csrfToken
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor to handle errors
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Handle unauthorized access
      localStorage.removeItem('auth_token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// API service
export const api = {
  // File upload and analysis
  uploadFile(file, prompt, description = '', reportOptions = {}) {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('prompt', prompt)
    if (reportOptions?.dataset_type) {
      formData.append('dataset_type', reportOptions.dataset_type)
    }
    if (description) {
      formData.append('description', description)
    }
    if (reportOptions && Object.keys(reportOptions).length) {
      formData.append('report_options', JSON.stringify(reportOptions))
    }

    return apiClient.post('/simple-upload/', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: LONG_REQUEST_TIMEOUT,
    })
  },

  getTaskStatus(taskId) {
    return apiClient.get(`/tasks/${taskId}/`)
  },

  // Reports
  getReports(params = {}) {
    return apiClient.get('/simple-reports/', { params })
  },

  getReport(id) {
    return apiClient.get(`/simple-reports/${id}/`)
  },

  getReportDetail(id) {
    return apiClient.get(`/simple-reports/${id}/`)
  },

  getReportMetrics(id) {
    return apiClient.get(`/reports/${id}/metrics/`)
  },

  getReportTrends(id) {
    return apiClient.get(`/reports/${id}/trends/`)
  },

  getReportBenchmark(id) {
    return apiClient.get(`/reports/${id}/benchmark/`)
  },

  getReportInsights(id) {
    return apiClient.get(`/reports/${id}/insights/`)
  },

  regenerateInsights(id, reportOptions = {}) {
    return apiClient.post(
      `/reports/${id}/insights/regenerate/`,
      { report_options: reportOptions },
      { timeout: LONG_REQUEST_TIMEOUT },
    )
  },

  getReportPromptConfig() {
    return apiClient.get('/simple-reports/prompt-config/')
  },

  updateReportPromptConfig(config) {
    return apiClient.post('/simple-reports/prompt-config/update/', config)
  },

  getAnalysisPrompts() {
    return apiClient.get('/analysis-prompts/')
  },

  updateAnalysisPrompt(promptId, content) {
    return apiClient.post('/analysis-prompts/update/', {
      prompt_id: promptId,
      content,
    })
  },

  resetAnalysisPrompt(promptId = 'all') {
    return apiClient.post('/analysis-prompts/reset/', {
      prompt_id: promptId,
    })
  },

  getPromptModules(params = {}) {
    return apiClient.get('/prompt-modules/', { params })
  },

  getPromptModule(id) {
    return apiClient.get(`/prompt-modules/${id}/`)
  },

  updatePromptModule(id, payload) {
    return apiClient.post(`/prompt-modules/${id}/update/`, payload)
  },

  getPromptModuleVersions(id) {
    return apiClient.get(`/prompt-modules/${id}/versions/`)
  },

  comparePromptModuleVersions(id, fromVersion, toVersion) {
    return apiClient.get(`/prompt-modules/${id}/versions/compare/`, {
      params: { from: fromVersion, to: toVersion },
    })
  },

  restorePromptModuleVersion(id, versionNumber) {
    return apiClient.post(`/prompt-modules/${id}/restore/`, {
      version_number: versionNumber,
    })
  },

  regenerateReportSection(reportId, sectionKey, payload = {}) {
    return apiClient.post(
      `/simple-reports/${reportId}/sections/${encodeURIComponent(sectionKey)}/regenerate/`,
      payload,
      { timeout: LONG_REQUEST_TIMEOUT },
    )
  },

  getReportSectionHistory(reportId, sectionKey) {
    return apiClient.get(
      `/simple-reports/${reportId}/sections/${encodeURIComponent(sectionKey)}/history/`,
    )
  },

  saveMasterPromptToDatasetPrompt(reportId, masterPrompt) {
    return apiClient.post(
      `/simple-reports/${reportId}/save-master-prompt/`,
      { master_prompt: masterPrompt },
      { timeout: 30000 },
    )
  },

  getReportSectionMappings(reportId) {
    return apiClient.get(`/simple-reports/${reportId}/section-mappings/`)
  },

  // Per-user settings
  getUserSettings() {
    return apiClient.get('/user-settings/')
  },

  updateUserSettings(settings) {
    return apiClient.post('/user-settings/update/', settings)
  },

  previewCleanup() {
    return apiClient.get('/cleanup/preview/')
  },

  runCleanup(dryRun = true) {
    return apiClient.post(`/cleanup/?dry_run=${dryRun ? 'true' : 'false'}`)
  },

  getManageableReports(params = {}) {
    return apiClient.get('/reports/manage/', { params })
  },

  bulkReportAction(action, reportIds) {
    return apiClient.post('/reports/manage/bulk-action/', {
      action,
      report_ids: reportIds,
    })
  },

  exportReport(id, format = 'pdf') {
    return downloadReportFile(id, format)
  },

  // User data
  getUserUploads() {
    return apiClient.get('/uploads/')
  },

  // System
  getSystemHealth() {
    return apiClient.get('/health/')
  },

  getCurrentUser() {
    return apiClient.get('/auth/me/')
  },

  // Authentication
  login(credentials) {
    // Try simple login endpoint first
    return apiClient.post('/simple-login/', credentials)
  },
}

const FILE_EXTENSIONS = {
  json: 'json',
  pdf: 'pdf',
  csv: 'csv',
  word: 'docx',
  excel: 'xlsx',
}

const EXPORT_PATH_FORMAT = {
  pdf: 'print',
  word: 'editable',
  json: 'json',
  csv: 'csv',
  excel: 'excel',
}

export function getReportDownloadUrl(reportId, format = 'pdf') {
  const baseUrl = API_BASE_URL.replace(/\/$/, '')
  const pathFormat = EXPORT_PATH_FORMAT[format] || format
  return `${baseUrl}/media/${reportId}/${encodeURIComponent(pathFormat)}/`
}

/** Prefer native <a href> in templates; this is for programmatic export (e.g. export all). */
export function downloadReportFile(reportId, format = 'pdf') {
  const url = getReportDownloadUrl(reportId, format)
  const filename = `financial_report_${reportId}.${FILE_EXTENSIONS[format] || format}`

  window.open(url, '_blank', 'noopener,noreferrer')

  return Promise.resolve({
    data: null,
    headers: { 'content-disposition': `attachment; filename="${filename}"` },
  })
}

// Error handling utility
export class ApiError extends Error {
  constructor(message, status, data) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

// Utility function to handle API errors
export const handleApiError = (error) => {
  if (error.response) {
    // Server responded with error status
    const { status, data } = error.response
    const message = data.error || data.message || 'An error occurred'
    throw new ApiError(message, status, data)
  } else if (error.request) {
    const blocked = error.message === 'Network Error' || error.code === 'ERR_NETWORK'
    const message = blocked
      ? 'Download was blocked by the browser or an extension (e.g. ad blocker). Allow localhost or try another browser.'
      : 'Network error. Please check your connection.'
    throw new ApiError(message, 0)
  } else {
    // Other error
    throw new ApiError(error.message || 'An unexpected error occurred', 0)
  }
}

// Utility function to format file sizes
export const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// Utility function to format dates
export const formatDate = (dateString) => {
  const date = new Date(dateString)
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Utility function to format percentages
export const formatPercentage = (value, decimals = 2) => {
  if (value === null || value === undefined) return 'N/A'
  return `${parseFloat(value).toFixed(decimals)}%`
}

// Utility function to format currency
export const formatCurrency = (value, currency = 'USD') => {
  if (value === null || value === undefined) return 'N/A'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
  }).format(value)
}

// Utility function to get risk level color
export const getRiskLevelColor = (riskLevel) => {
  const colors = {
    low: 'success',
    moderate: 'warning',
    high: 'danger',
    critical: 'danger',
  }
  return colors[riskLevel] || 'info'
}

// Utility function to get score color
export const getScoreColor = (score) => {
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'danger'
}

// Utility function to get status badge class
export const getStatusBadgeClass = (status) => {
  const classes = {
    completed: 'status-success',
    processing: 'status-info',
    pending: 'status-warning',
    failed: 'status-danger',
  }
  return classes[status] || 'status-info'
}

// No mock data - using real API endpoints only

export default api
