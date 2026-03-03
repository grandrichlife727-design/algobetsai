import axios, { AxiosInstance } from 'axios'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://algobetsai.onrender.com'

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
})

// Add request interceptor for auth token if needed
apiClient.interceptors.request.use(
  (config) => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Add response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Handle unauthorized
      if (typeof window !== 'undefined') {
        localStorage.removeItem('auth_token')
      }
    }
    return Promise.reject(error)
  }
)

export const api = {
  // Picks endpoints
  getPicks: async (filters?: { sport?: string; status?: string; limit?: number }) => {
    const response = await apiClient.get('/api/picks', { params: filters })
    return response.data
  },

  getPickById: async (id: string) => {
    const response = await apiClient.get(`/api/picks/${id}`)
    return response.data
  },

  createPick: async (data: any) => {
    const response = await apiClient.post('/api/picks', data)
    return response.data
  },

  updatePickStatus: async (id: string, status: string) => {
    const response = await apiClient.patch(`/api/picks/${id}`, { status })
    return response.data
  },

  // Predictions endpoint
  getPredictions: async (filters?: any) => {
    const response = await apiClient.get('/api/predictions', { params: filters })
    return response.data
  },

  // Performance endpoints
  getPerformanceStats: async () => {
    const response = await apiClient.get('/api/performance')
    return response.data
  },

  getPerformanceHistory: async (sport?: string) => {
    const response = await apiClient.get('/api/performance/history', { params: { sport } })
    return response.data
  },

  // Odds endpoints
  getOdds: async (event?: string) => {
    const response = await apiClient.get('/api/odds', { params: { event } })
    return response.data
  },

  // Alerts endpoints
  getAlerts: async (limit?: number) => {
    const response = await apiClient.get('/api/alerts', { params: { limit } })
    return response.data
  },

  markAlertAsRead: async (id: string) => {
    const response = await apiClient.patch(`/api/alerts/${id}/read`)
    return response.data
  },

  // Health check
  healthCheck: async () => {
    try {
      const response = await apiClient.get('/health')
      return response.status === 200
    } catch {
      return false
    }
  },
}

export default apiClient
