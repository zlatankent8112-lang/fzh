/**
 * M-PESA Real-time Transaction Status Polling
 * Automatically checks transaction status and updates UI without page refresh
 */

class MpesaStatusPoller {
  constructor(transactionId, options = {}) {
    this.transactionId = transactionId;
    this.pollInterval = options.pollInterval || 3000; // Poll every 3 seconds
    this.maxAttempts = options.maxAttempts || 40; // Max 2 minutes (40 * 3s)
    this.attempts = 0;
    this.timerId = null;
    this.onStatusChange = options.onStatusChange || this.defaultStatusHandler;
    this.onComplete = options.onComplete || this.defaultCompleteHandler;
    this.onError = options.onError || this.defaultErrorHandler;
  }

  /**
   * Start polling for transaction status
   */
  start() {
    console.log(
      `Starting status polling for transaction #${this.transactionId}`
    );
    this.poll();
  }

  /**
   * Stop polling
   */
  stop() {
    if (this.timerId) {
      clearTimeout(this.timerId);
      this.timerId = null;
      console.log(`Stopped polling for transaction #${this.transactionId}`);
    }
  }

  /**
   * Poll transaction status
   */
  async poll() {
    try {
      this.attempts++;

      // Check if max attempts reached
      if (this.attempts > this.maxAttempts) {
        this.onError({
          message:
            "Transaction status check timed out. Please refresh the page.",
          timeout: true,
        });
        this.stop();
        return;
      }

      // Fetch status
      const response = await fetch(
        `/mpesa/transaction-status/${this.transactionId}`
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        this.onError(data);
        this.stop();
        return;
      }

      // Update UI with current status
      this.onStatusChange(data);

      // Check if transaction is complete
      if (data.completed) {
        this.onComplete(data);
        this.stop();
        return;
      }

      // Schedule next poll
      this.timerId = setTimeout(() => this.poll(), this.pollInterval);
    } catch (error) {
      console.error("Error polling transaction status:", error);
      this.onError({ message: error.message, error: true });

      // Retry after delay if not max attempts
      if (this.attempts < this.maxAttempts) {
        this.timerId = setTimeout(() => this.poll(), this.pollInterval);
      } else {
        this.stop();
      }
    }
  }

  /**
   * Default status change handler
   */
  defaultStatusHandler(data) {
    console.log("Transaction status:", data.status, data);

    // Update status message on page if element exists
    const statusElement = document.getElementById("transaction-status");
    if (statusElement) {
      let statusHTML = `
                <div class="alert alert-info">
                    <strong>Status:</strong> ${this.getStatusBadge(data.status)}
                    <br>
                    <span>${data.message || "Processing..."}</span>
                </div>
            `;
      statusElement.innerHTML = statusHTML;
    }
  }

  /**
   * Default completion handler
   */
  defaultCompleteHandler(data) {
    console.log("Transaction complete:", data);

    const statusElement = document.getElementById("transaction-status");
    if (statusElement) {
      let alertClass =
        data.status === "success" ? "alert-success" : "alert-danger";
      let icon = data.status === "success" ? "✅" : "❌";

      let statusHTML = `
                <div class="alert ${alertClass}">
                    <h4>${icon} ${
        data.status === "success" ? "Payment Successful!" : "Payment Failed"
      }</h4>
                    <p>${data.message}</p>
                    ${
                      data.receipt_number
                        ? `<p><strong>Receipt:</strong> ${data.receipt_number}</p>`
                        : ""
                    }
                </div>
            `;
      statusElement.innerHTML = statusHTML;
    }

    // Show success/failure notification
    if (typeof showNotification === "function") {
      showNotification(
        data.message,
        data.status === "success" ? "success" : "error"
      );
    }
  }

  /**
   * Default error handler
   */
  defaultErrorHandler(error) {
    console.error("Transaction status error:", error);

    const statusElement = document.getElementById("transaction-status");
    if (statusElement) {
      statusElement.innerHTML = `
                <div class="alert alert-warning">
                    <strong>Error:</strong> ${
                      error.message || "Failed to check transaction status"
                    }
                    <br>
                    <small>Please refresh the page to see the latest status.</small>
                </div>
            `;
    }
  }

  /**
   * Get status badge HTML
   */
  getStatusBadge(status) {
    const badges = {
      pending: '<span class="badge badge-warning">⏳ Pending</span>',
      success: '<span class="badge badge-success">✅ Success</span>',
      failed: '<span class="badge badge-danger">❌ Failed</span>',
      cancelled: '<span class="badge badge-secondary">🚫 Cancelled</span>',
      timeout: '<span class="badge badge-warning">⏱️ Timeout</span>',
    };
    return badges[status] || `<span class="badge badge-info">${status}</span>`;
  }
}

/**
 * Helper function to show notification
 */
function showNotification(message, type = "info") {
  // Try to use toast notification if available
  if (typeof toastr !== "undefined") {
    toastr[type](message);
    return;
  }

  // Fallback to alert
  if (type === "success") {
    alert("✅ " + message);
  } else if (type === "error") {
    alert("❌ " + message);
  } else {
    alert(message);
  }
}

/**
 * Initialize status polling when page loads
 */
document.addEventListener("DOMContentLoaded", function () {
  // Check if there's a transaction ID to poll
  const transactionElement = document.getElementById("mpesa-transaction-id");
  if (transactionElement) {
    const transactionId =
      transactionElement.value || transactionElement.dataset.transactionId;

    if (transactionId) {
      console.log(
        "Initializing M-PESA status poller for transaction:",
        transactionId
      );

      const poller = new MpesaStatusPoller(transactionId, {
        onComplete: function (data) {
          console.log("Payment complete:", data);

          // Update UI
          const statusElement = document.getElementById("transaction-status");
          if (statusElement) {
            if (data.status === "success") {
              statusElement.innerHTML = `
                                <div class="alert alert-success">
                                    <h4>✅ Payment Successful!</h4>
                                    <p><strong>Amount:</strong> KES ${data.amount.toFixed(
                                      2
                                    )}</p>
                                    <p><strong>Receipt:</strong> ${
                                      data.receipt_number
                                    }</p>
                                    <p><strong>Date:</strong> ${new Date(
                                      data.transaction_date
                                    ).toLocaleString()}</p>
                                    <br>
                                    <a href="/fees/payment-history" class="btn btn-primary">View Payment History</a>
                                </div>
                            `;
            } else {
              statusElement.innerHTML = `
                                <div class="alert alert-danger">
                                    <h4>❌ Payment ${data.status}</h4>
                                    <p>${data.message}</p>
                                    <br>
                                    <button onclick="location.reload()" class="btn btn-warning">Try Again</button>
                                </div>
                            `;
            }
          }

          // Show notification
          showNotification(
            data.message,
            data.status === "success" ? "success" : "error"
          );

          // Refresh page after 5 seconds on success
          if (data.status === "success") {
            setTimeout(() => {
              window.location.reload();
            }, 5000);
          }
        },
      });

      poller.start();

      // Store poller instance for manual control if needed
      window.mpesaPoller = poller;
    }
  }
});
