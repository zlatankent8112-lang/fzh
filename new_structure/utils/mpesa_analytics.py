"""
M-PESA Analytics Service
Provides statistics and insights on M-PESA transactions.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, extract, case
from new_structure.models.fee_management import MpesaTransaction, Payment
from new_structure.extensions import db

logger = logging.getLogger(__name__)


class MpesaAnalytics:
    """Service for M-PESA payment analytics and statistics."""
    
    @staticmethod
    def get_dashboard_stats(days=30, start_date=None, end_date=None, status=None, 
                          min_amount=None, max_amount=None, student_id=None):
        """
        Get comprehensive M-PESA dashboard statistics.
        
        Args:
            days: Number of days to include in analysis (default: 30)
            start_date: Optional start date for custom range
            end_date: Optional end date for custom range
            status: Filter by transaction status
            min_amount: Filter by minimum amount
            max_amount: Filter by maximum amount
            student_id: Filter by specific student
            
        Returns:
            Dictionary with various statistics and trends
        """
        try:
            # Determine date range
            if start_date and end_date:
                cutoff_date = start_date
                end_filter = end_date
            else:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                end_filter = datetime.utcnow()
            
            # Build base query
            base_query = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.created_at <= end_filter
            )
            
            # Apply filters
            if status:
                base_query = base_query.filter(MpesaTransaction.status == status)
            if min_amount is not None:
                base_query = base_query.filter(MpesaTransaction.amount >= min_amount)
            if max_amount is not None:
                base_query = base_query.filter(MpesaTransaction.amount <= max_amount)
            if student_id is not None:
                base_query = base_query.filter(MpesaTransaction.student_id == student_id)
            
            # Basic transaction counts
            total_transactions = base_query.count()
            
            # If filtering by status, just use base_query
            if status:
                successful = base_query.count() if status == 'success' else 0
                failed = base_query.count() if status == 'failed' else 0
                pending = base_query.count() if status == 'pending' else 0
                timed_out = base_query.count() if status == 'timeout' else 0
                cancelled = base_query.count() if status == 'cancelled' else 0
            else:
                # Build filtered queries for each status
                successful_query = MpesaTransaction.query.filter(
                    MpesaTransaction.created_at >= cutoff_date,
                    MpesaTransaction.created_at <= end_filter,
                    MpesaTransaction.status == 'success'
                )
                if min_amount is not None:
                    successful_query = successful_query.filter(MpesaTransaction.amount >= min_amount)
                if max_amount is not None:
                    successful_query = successful_query.filter(MpesaTransaction.amount <= max_amount)
                if student_id is not None:
                    successful_query = successful_query.filter(MpesaTransaction.student_id == student_id)
                successful = successful_query.count()
                
                failed_query = MpesaTransaction.query.filter(
                    MpesaTransaction.created_at >= cutoff_date,
                    MpesaTransaction.created_at <= end_filter,
                    MpesaTransaction.status == 'failed'
                )
                if min_amount is not None:
                    failed_query = failed_query.filter(MpesaTransaction.amount >= min_amount)
                if max_amount is not None:
                    failed_query = failed_query.filter(MpesaTransaction.amount <= max_amount)
                if student_id is not None:
                    failed_query = failed_query.filter(MpesaTransaction.student_id == student_id)
                failed = failed_query.count()
                
                pending = MpesaTransaction.query.filter(
                    MpesaTransaction.created_at >= cutoff_date,
                    MpesaTransaction.created_at <= end_filter,
                    MpesaTransaction.status == 'pending'
                ).count()
                
                timed_out = MpesaTransaction.query.filter(
                    MpesaTransaction.created_at >= cutoff_date,
                    MpesaTransaction.created_at <= end_filter,
                    MpesaTransaction.status == 'timeout'
                ).count()
                
                cancelled = MpesaTransaction.query.filter(
                    MpesaTransaction.created_at >= cutoff_date,
                    MpesaTransaction.created_at <= end_filter,
                    MpesaTransaction.status == 'cancelled'
                ).count()
            
            # Financial statistics - use success query
            success_query = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.created_at <= end_filter,
                MpesaTransaction.status == 'success'
            )
            if min_amount is not None:
                success_query = success_query.filter(MpesaTransaction.amount >= min_amount)
            if max_amount is not None:
                success_query = success_query.filter(MpesaTransaction.amount <= max_amount)
            if student_id is not None:
                success_query = success_query.filter(MpesaTransaction.student_id == student_id)
            
            total_amount_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.id.in_([t.id for t in success_query.all()])
            ).scalar() if success_query.count() > 0 else 0
            
            total_amount = float(total_amount_result or 0)
            
            # Average transaction amount
            avg_amount_result = db.session.query(
                func.avg(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.id.in_([t.id for t in success_query.all()])
            ).scalar() if success_query.count() > 0 else 0
            
            avg_amount = float(avg_amount_result or 0)
            
            # Success rate
            success_rate = (successful / total_transactions * 100) if total_transactions > 0 else 0
            
            # Today's statistics
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_transactions = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= today_start
            ).count()
            
            today_successful = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= today_start,
                MpesaTransaction.status == 'success'
            ).count()
            
            today_amount_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= today_start,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            today_amount = float(today_amount_result or 0)
            
            return {
                'period_days': days,
                'total_transactions': total_transactions,
                'successful_transactions': successful,  # Added for test compatibility
                'successful': successful,
                'failed_transactions': failed,  # Added for test compatibility
                'failed': failed,
                'pending_transactions': pending,  # Added for test compatibility
                'pending': pending,
                'timed_out': timed_out,
                'cancelled': cancelled,
                'success_rate': round(success_rate, 2),
                'total_amount': round(total_amount, 2),
                'avg_amount': round(avg_amount, 2),
                'today': {
                    'transactions': today_transactions,
                    'successful': today_successful,
                    'amount': round(today_amount, 2)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
            return {
                'error': str(e),
                'total_transactions': 0,
                'successful': 0,
                'failed': 0,
                'pending': 0,
                'success_rate': 0,
                'total_amount': 0,
                'avg_amount': 0
            }
    
    @staticmethod
    def get_daily_trends(days=30):
        """
        Get daily transaction trends.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            List of daily statistics
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Query daily aggregates
            daily_data = db.session.query(
                func.date(MpesaTransaction.created_at).label('date'),
                func.count(MpesaTransaction.id).label('count'),
                func.sum(
                    case(
                        (MpesaTransaction.status == 'success', MpesaTransaction.amount),
                        else_=0
                    )
                ).label('total_amount'),
                func.sum(
                    case((MpesaTransaction.status == 'success', 1), else_=0)
                ).label('successful'),
                func.sum(
                    case((MpesaTransaction.status == 'failed', 1), else_=0)
                ).label('failed')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date
            ).group_by(
                func.date(MpesaTransaction.created_at)
            ).order_by(
                func.date(MpesaTransaction.created_at)
            ).all()
            
            # Format results
            trends = []
            for row in daily_data:
                trends.append({
                    'date': row.date.isoformat() if row.date else None,
                    'count': row.count,
                    'amount': float(row.total_amount or 0),
                    'successful': row.successful,
                    'failed': row.failed
                })
            
            return trends
            
        except Exception as e:
            logger.error(f"Error getting daily trends: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_hourly_distribution():
        """
        Get hourly distribution of transactions (which hours have most payments).
        
        Returns:
            List of hourly statistics
        """
        try:
            # Query hourly distribution for last 30 days
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            
            hourly_data = db.session.query(
                extract('hour', MpesaTransaction.created_at).label('hour'),
                func.count(MpesaTransaction.id).label('count'),
                func.sum(
                    case((MpesaTransaction.status == 'success', 1), else_=0)
                ).label('successful')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date
            ).group_by(
                extract('hour', MpesaTransaction.created_at)
            ).order_by(
                extract('hour', MpesaTransaction.created_at)
            ).all()
            
            # Format results
            distribution = []
            for row in hourly_data:
                distribution.append({
                    'hour': int(row.hour),
                    'transaction_count': row.count,  # Renamed for test compatibility
                    'count': row.count,
                    'successful': row.successful
                })
            
            return distribution
            
        except Exception as e:
            logger.error(f"Error getting hourly distribution: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_top_paying_students(limit=10, days=30):
        """
        Get students with the most M-PESA payments.
        
        Args:
            limit: Number of students to return
            days: Period to analyze
            
        Returns:
            List of top paying students
        """
        try:
            from new_structure.models.academic import Student
            
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            top_students = db.session.query(
                Student.id,
                Student.name,
                Student.admission_number,
                func.count(MpesaTransaction.id).label('payment_count'),
                func.sum(MpesaTransaction.amount).label('total_amount')
            ).join(
                MpesaTransaction, Student.id == MpesaTransaction.student_id
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).group_by(
                Student.id, Student.name, Student.admission_number
            ).order_by(
                func.sum(MpesaTransaction.amount).desc()
            ).limit(limit).all()
            
            # Format results
            students = []
            for row in top_students:
                students.append({
                    'student_id': row.id,
                    'name': row.name,
                    'admission_number': row.admission_number,
                    'payment_count': row.payment_count,
                    'total_amount': float(row.total_amount or 0)
                })
            
            return students
            
        except Exception as e:
            logger.error(f"Error getting top paying students: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_recent_transactions(limit=10):
        """
        Get recent M-PESA transactions.
        
        Args:
            limit: Number of transactions to return
            
        Returns:
            List of recent transactions
        """
        try:
            from new_structure.models.academic import Student
            
            recent = db.session.query(
                MpesaTransaction,
                Student.name.label('student_name'),
                Student.admission_number
            ).outerjoin(
                Student, MpesaTransaction.student_id == Student.id
            ).order_by(
                MpesaTransaction.created_at.desc()
            ).limit(limit).all()
            
            # Format results
            transactions = []
            for tx, student_name, admission_number in recent:
                transactions.append({
                    'id': tx.id,
                    'amount': float(tx.amount),
                    'phone_number': tx.phone_number,
                    'status': tx.status,
                    'created_at': tx.created_at.isoformat() if tx.created_at else None,
                    'mpesa_receipt_number': tx.mpesa_receipt_number,
                    'student_name': student_name,
                    'admission_number': admission_number
                })
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting recent transactions: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_failure_analysis(days=30):
        """
        Analyze failed transactions to identify patterns.
        
        Args:
            days: Period to analyze
            
        Returns:
            Dictionary with failure analysis
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Get failed transactions grouped by result description
            failed_reasons = db.session.query(
                MpesaTransaction.result_desc,
                func.count(MpesaTransaction.id).label('count')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'failed',
                MpesaTransaction.result_desc.isnot(None)
            ).group_by(
                MpesaTransaction.result_desc
            ).order_by(
                func.count(MpesaTransaction.id).desc()
            ).all()
            
            # Format results
            reasons = []
            for row in failed_reasons:
                reasons.append({
                    'reason': row.result_desc,
                    'count': row.count
                })
            
            return {
                'total_failed': sum(r['count'] for r in reasons),
                'failure_reasons': reasons
            }
            
        except Exception as e:
            logger.error(f"Error analyzing failures: {e}", exc_info=True)
            return {
                'total_failed': 0,
                'failure_reasons': []
            }
    
    @staticmethod
    def get_failure_rate(days=30):
        """
        Calculate the failure rate as a percentage.
        
        Args:
            days: Period to analyze
            
        Returns:
            Float: Failure rate percentage (0-100)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            total = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date
            ).count()
            
            failed = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'failed'
            ).count()
            
            if total == 0:
                return 0.0
            
            failure_rate = (failed / total) * 100
            return round(failure_rate, 2)
            
        except Exception as e:
            logger.error(f"Error calculating failure rate: {e}", exc_info=True)
            return 0.0
    
    @staticmethod
    def get_failure_reasons(days=30):
        """
        Get detailed failure reasons grouped by result description.
        
        Args:
            days: Period to analyze
            
        Returns:
            List of failure reasons with counts
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            failure_reasons = db.session.query(
                MpesaTransaction.result_desc,
                func.count(MpesaTransaction.id).label('count')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'failed',
                MpesaTransaction.result_desc.isnot(None)
            ).group_by(
                MpesaTransaction.result_desc
            ).order_by(
                func.count(MpesaTransaction.id).desc()
            ).all()
            
            reasons = []
            for row in failure_reasons:
                reasons.append({
                    'reason': row.result_desc,
                    'count': row.count
                })
            
            return reasons
            
        except Exception as e:
            logger.error(f"Error getting failure reasons: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_timeout_transactions(timeout_minutes=2):
        """
        Get transactions that have timed out (pending with no callback).
        
        Args:
            timeout_minutes: Minutes to consider a transaction timed out
            
        Returns:
            List of timed out transactions
        """
        try:
            timeout_threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            
            timed_out = MpesaTransaction.query.filter(
                MpesaTransaction.status == 'pending',
                MpesaTransaction.created_at < timeout_threshold,
                MpesaTransaction.callback_received == False
            ).all()
            
            transactions = []
            for tx in timed_out:
                transactions.append({
                    'id': tx.id,
                    'phone_number': tx.phone_number,
                    'amount': float(tx.amount),
                    'created_at': tx.created_at.isoformat() if tx.created_at else None,
                    'checkout_request_id': tx.checkout_request_id
                })
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting timeout transactions: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_monthly_revenue(months=6):
        """
        Calculate total revenue for the last N months.
        
        Args:
            months: Number of months to analyze
            
        Returns:
            Float: Total revenue
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=months * 30)
            
            revenue_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            return float(revenue_result or 0)
            
        except Exception as e:
            logger.error(f"Error calculating monthly revenue: {e}", exc_info=True)
            return 0.0
    
    @staticmethod
    def get_daily_average_revenue(days=30):
        """
        Calculate average daily revenue.
        
        Args:
            days: Period to analyze
            
        Returns:
            Float: Average daily revenue
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            total_revenue_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            total_revenue = float(total_revenue_result or 0)
            
            if days == 0:
                return 0.0
            
            avg_revenue = total_revenue / days
            return round(avg_revenue, 2)
            
        except Exception as e:
            logger.error(f"Error calculating daily average revenue: {e}", exc_info=True)
            return 0.0
    
    @staticmethod
    def get_revenue_by_payment_method(days=30):
        """
        Get revenue breakdown by payment method (M-PESA only for now).
        
        Args:
            days: Period to analyze
            
        Returns:
            Dict or List: Revenue breakdown
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            mpesa_revenue_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            mpesa_revenue = float(mpesa_revenue_result or 0)
            
            return {
                'M-PESA': mpesa_revenue
            }
            
        except Exception as e:
            logger.error(f"Error getting revenue by payment method: {e}", exc_info=True)
            return {}
    
    @staticmethod
    def export_to_csv(start_date=None, end_date=None, date_from=None, date_to=None):
        """
        Export M-PESA transactions to CSV format.
        
        Args:
            start_date: Start date for export (preferred)
            end_date: End date for export (preferred)
            date_from: Alias for start_date (backward compatibility)
            date_to: Alias for end_date (backward compatibility)
            
        Returns:
            String: CSV formatted data
        """
        try:
            import csv
            from io import StringIO
            
            # Use start_date/end_date if provided, otherwise use date_from/date_to
            start = start_date or date_from
            end = end_date or date_to
            
            # Build query
            query = MpesaTransaction.query
            
            if start:
                query = query.filter(MpesaTransaction.created_at >= start)
            if end:
                query = query.filter(MpesaTransaction.created_at <= end)
            
            transactions = query.order_by(MpesaTransaction.created_at.desc()).all()
            
            # Create CSV in memory
            output = StringIO()
            writer = csv.writer(output)
            
            # Write header
            writer.writerow([
                'ID', 'Phone Number', 'Amount', 'Status', 'Receipt Number',
                'Merchant Request ID', 'Checkout Request ID', 'Result Code',
                'Result Description', 'Created At', 'Transaction Date'
            ])
            
            # Write data
            for tx in transactions:
                writer.writerow([
                    tx.id,
                    tx.phone_number,
                    tx.amount,
                    tx.status,
                    tx.mpesa_receipt_number or '',
                    tx.merchant_request_id or '',
                    tx.checkout_request_id or '',
                    tx.result_code or '',
                    tx.result_desc or '',
                    tx.created_at.isoformat() if tx.created_at else '',
                    tx.transaction_date.isoformat() if tx.transaction_date else ''
                ])
            
            return output.getvalue()
            
        except Exception as e:
            logger.error(f"Error exporting to CSV: {e}", exc_info=True)
            return ""
    
    @staticmethod
    def export_to_excel(start_date=None, end_date=None, date_from=None, date_to=None):
        """
        Export M-PESA transactions to Excel format.
        
        Args:
            start_date: Start date for export (preferred)
            end_date: End date for export (preferred)
            date_from: Alias for start_date (backward compatibility)
            date_to: Alias for end_date (backward compatibility)
            
        Returns:
            BytesIO: Excel file data
        """
        try:
            from io import BytesIO
            
            # Try openpyxl, fall back to xlsxwriter
            try:
                from openpyxl import Workbook
                use_openpyxl = True
            except ImportError:
                try:
                    import xlsxwriter
                    use_openpyxl = False
                except ImportError:
                    logger.warning("Neither openpyxl nor xlsxwriter installed. Cannot export to Excel.")
                    return None
            
            # Use start_date/end_date if provided, otherwise use date_from/date_to
            start = start_date or date_from
            end = end_date or date_to
            
            # Build query
            query = MpesaTransaction.query
            
            if start:
                query = query.filter(MpesaTransaction.created_at >= start)
            if end:
                query = query.filter(MpesaTransaction.created_at <= end)
            
            transactions = query.order_by(MpesaTransaction.created_at.desc()).all()
            
            # Create Excel file
            output = BytesIO()
            
            if use_openpyxl:
                # Using openpyxl
                wb = Workbook()
                ws = wb.active
                ws.title = "M-PESA Transactions"
                
                # Write header
                headers = [
                    'ID', 'Phone Number', 'Amount', 'Status', 'Receipt Number',
                    'Merchant Request ID', 'Checkout Request ID', 'Result Code',
                    'Result Description', 'Created At', 'Transaction Date'
                ]
                ws.append(headers)
                
                # Write data
                for tx in transactions:
                    ws.append([
                        tx.id,
                        tx.phone_number,
                        float(tx.amount) if tx.amount else 0,
                        tx.status,
                        tx.mpesa_receipt_number or '',
                        tx.merchant_request_id or '',
                        tx.checkout_request_id or '',
                        tx.result_code or '',
                        tx.result_desc or '',
                        tx.created_at.isoformat() if tx.created_at else '',
                        tx.transaction_date.isoformat() if tx.transaction_date else ''
                    ])
                
                wb.save(output)
            else:
                # Using xlsxwriter
                import xlsxwriter
                
                workbook = xlsxwriter.Workbook(output, {'in_memory': True})
                worksheet = workbook.add_worksheet('M-PESA Transactions')
                
                # Write header
                headers = [
                    'ID', 'Phone Number', 'Amount', 'Status', 'Receipt Number',
                    'Merchant Request ID', 'Checkout Request ID', 'Result Code',
                    'Result Description', 'Created At', 'Transaction Date'
                ]
                
                for col, header in enumerate(headers):
                    worksheet.write(0, col, header)
                
                # Write data
                for row_idx, tx in enumerate(transactions, start=1):
                    worksheet.write(row_idx, 0, tx.id)
                    worksheet.write(row_idx, 1, tx.phone_number)
                    worksheet.write(row_idx, 2, float(tx.amount) if tx.amount else 0)
                    worksheet.write(row_idx, 3, tx.status)
                    worksheet.write(row_idx, 4, tx.mpesa_receipt_number or '')
                    worksheet.write(row_idx, 5, tx.merchant_request_id or '')
                    worksheet.write(row_idx, 6, tx.checkout_request_id or '')
                    worksheet.write(row_idx, 7, tx.result_code or '')
                    worksheet.write(row_idx, 8, tx.result_desc or '')
                    worksheet.write(row_idx, 9, tx.created_at.isoformat() if tx.created_at else '')
                    worksheet.write(row_idx, 10, tx.transaction_date.isoformat() if tx.transaction_date else '')
                
                workbook.close()
            
            output.seek(0)
            return output
            
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}", exc_info=True)
            return None
    
    @staticmethod
    def get_student_payment_history(student_id, days=90):
        """
        Get M-PESA payment history for a specific student.
        
        Args:
            student_id: ID of the student
            days: Number of days to look back
            
        Returns:
            List of transactions for the student
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            transactions = MpesaTransaction.query.filter(
                MpesaTransaction.student_id == student_id,
                MpesaTransaction.created_at >= cutoff_date
            ).order_by(
                MpesaTransaction.created_at.desc()
            ).all()
            
            history = []
            for tx in transactions:
                history.append({
                    'id': tx.id,
                    'amount': float(tx.amount) if tx.amount else 0,
                    'status': tx.status,
                    'phone_number': tx.phone_number,
                    'mpesa_receipt_number': tx.mpesa_receipt_number,
                    'created_at': tx.created_at.isoformat() if tx.created_at else None,
                    'transaction_date': tx.transaction_date.isoformat() if tx.transaction_date else None,
                    'result_desc': tx.result_desc
                })
            
            return history
            
        except Exception as e:
            logger.error(f"Error getting student payment history: {e}", exc_info=True)
            return []
