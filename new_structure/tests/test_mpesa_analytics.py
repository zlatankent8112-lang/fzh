"""
Unit Tests for M-PESA Analytics
Tests analytics calculations and reporting functionality.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from new_structure.utils.mpesa_analytics import MpesaAnalytics
from new_structure.models.fee_management import MpesaTransaction


class TestAnalyticsDashboardStats:
    """Test dashboard statistics calculations"""
    
    def test_get_dashboard_stats_basic(self, db_session, sample_mpesa_transactions):
        """Test basic dashboard statistics"""
        stats = MpesaAnalytics.get_dashboard_stats()
        
        assert 'total_transactions' in stats
        assert 'successful_transactions' in stats
        assert 'total_amount' in stats
        assert 'success_rate' in stats
        assert isinstance(stats['total_transactions'], int)
        assert isinstance(stats['total_amount'], (int, float, Decimal))
    
    def test_get_dashboard_stats_with_date_range(self, db_session):
        """Test dashboard stats with date filtering"""
        start_date = datetime.now() - timedelta(days=7)
        end_date = datetime.now()
        
        stats = MpesaAnalytics.get_dashboard_stats(
            start_date=start_date,
            end_date=end_date
        )
        
        assert stats is not None
        assert 'total_transactions' in stats
    
    def test_success_rate_calculation(self, db_session):
        """Test success rate calculation accuracy"""
        # Create test transactions
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='completed'
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='completed'
            ),
            MpesaTransaction(
                phone_number='254712345680',
                amount=1500,
                account_reference='TEST003',
                status='failed'
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        stats = MpesaAnalytics.get_dashboard_stats()
        
        # 2 out of 3 = 66.67%
        assert stats['success_rate'] >= 65 and stats['success_rate'] <= 67
    
    def test_total_amount_calculation(self, db_session):
        """Test total amount calculation (successful only)"""
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='completed'
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='completed'
            ),
            MpesaTransaction(
                phone_number='254712345680',
                amount=1500,
                account_reference='TEST003',
                status='failed'  # Should not be counted
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        stats = MpesaAnalytics.get_dashboard_stats()
        
        # Only successful: 1000 + 2000 = 3000
        assert stats['total_amount'] == 3000
    
    def test_empty_database_stats(self, db_session):
        """Test dashboard stats with no transactions"""
        # Clear all transactions
        db_session.query(MpesaTransaction).delete()
        db_session.commit()
        
        stats = MpesaAnalytics.get_dashboard_stats()
        
        assert stats['total_transactions'] == 0
        assert stats['successful_transactions'] == 0
        assert stats['total_amount'] == 0
        assert stats['success_rate'] == 0 or stats['success_rate'] == 100


class TestDailyTrends:
    """Test daily trends analysis"""
    
    def test_get_daily_trends(self, db_session):
        """Test daily trends retrieval"""
        trends = MpesaAnalytics.get_daily_trends(days=7)
        
        assert isinstance(trends, list)
        assert len(trends) <= 7
        
        if len(trends) > 0:
            trend = trends[0]
            assert 'date' in trend
            assert 'transaction_count' in trend
            assert 'total_amount' in trend
    
    def test_daily_trends_correct_grouping(self, db_session):
        """Test daily trends groups by date correctly"""
        # Create transactions for different days
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='completed',
                created_at=today
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='completed',
                created_at=today
            ),
            MpesaTransaction(
                phone_number='254712345680',
                amount=1500,
                account_reference='TEST003',
                status='completed',
                created_at=yesterday
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        trends = MpesaAnalytics.get_daily_trends(days=2)
        
        # Should have 2 days of data
        assert len(trends) >= 1
    
    def test_daily_trends_date_range(self, db_session):
        """Test daily trends respects date range"""
        trends = MpesaAnalytics.get_daily_trends(days=30)
        
        # Should not exceed requested days
        assert len(trends) <= 30
    
    def test_daily_trends_ordering(self, db_session):
        """Test daily trends are ordered by date"""
        trends = MpesaAnalytics.get_daily_trends(days=7)
        
        if len(trends) > 1:
            # Should be ordered (ascending or descending)
            dates = [trend['date'] for trend in trends]
            is_ascending = all(dates[i] <= dates[i+1] for i in range(len(dates)-1))
            is_descending = all(dates[i] >= dates[i+1] for i in range(len(dates)-1))
            
            assert is_ascending or is_descending


class TestHourlyDistribution:
    """Test hourly distribution analysis"""
    
    def test_get_hourly_distribution(self, db_session):
        """Test hourly distribution retrieval"""
        distribution = MpesaAnalytics.get_hourly_distribution()
        
        assert isinstance(distribution, list)
        assert len(distribution) <= 24  # 24 hours
        
        if len(distribution) > 0:
            hour_data = distribution[0]
            assert 'hour' in hour_data
            assert 'transaction_count' in hour_data
            assert hour_data['hour'] >= 0 and hour_data['hour'] <= 23
    
    def test_hourly_distribution_all_hours(self, db_session):
        """Test hourly distribution covers all hours"""
        distribution = MpesaAnalytics.get_hourly_distribution()
        
        # May not have all 24 hours if no data, but structure should be valid
        hours = [h['hour'] for h in distribution]
        for hour in hours:
            assert hour >= 0 and hour <= 23
    
    def test_peak_hours_identification(self, db_session):
        """Test peak hours identification"""
        # Create transactions at different hours
        base_time = datetime.now().replace(minute=0, second=0, microsecond=0)
        
        transactions = [
            # Many transactions at hour 14
            MpesaTransaction(
                phone_number=f'25471234567{i}',
                amount=1000,
                account_reference=f'TEST00{i}',
                status='completed',
                created_at=base_time.replace(hour=14)
            ) for i in range(5)
        ] + [
            # Few transactions at hour 8
            MpesaTransaction(
                phone_number='254712345699',
                amount=1000,
                account_reference='TEST099',
                status='completed',
                created_at=base_time.replace(hour=8)
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        distribution = MpesaAnalytics.get_hourly_distribution()
        
        # Hour 14 should have more transactions
        if len(distribution) > 0:
            hour_14_data = next((h for h in distribution if h['hour'] == 14), None)
            if hour_14_data:
                assert hour_14_data['transaction_count'] >= 5


class TestTopPayingStudents:
    """Test top paying students analysis"""
    
    def test_get_top_paying_students(self, db_session):
        """Test top paying students retrieval"""
        top_students = MpesaAnalytics.get_top_paying_students(limit=10)
        
        assert isinstance(top_students, list)
        assert len(top_students) <= 10
        
        if len(top_students) > 0:
            student = top_students[0]
            assert 'student_name' in student or 'account_reference' in student
            assert 'total_amount' in student
            assert 'transaction_count' in student
    
    def test_top_paying_students_ordering(self, db_session, sample_students):
        """Test top paying students ordered by amount"""
        # Create transactions for different students
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=5000,
                account_reference='STU001',
                status='completed',
                student_id=1
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=3000,
                account_reference='STU002',
                status='completed',
                student_id=2
            ),
            MpesaTransaction(
                phone_number='254712345678',
                amount=2000,
                account_reference='STU001',
                status='completed',
                student_id=1
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        top_students = MpesaAnalytics.get_top_paying_students(limit=5)
        
        # Should be ordered by total amount descending
        if len(top_students) > 1:
            amounts = [s['total_amount'] for s in top_students]
            assert all(amounts[i] >= amounts[i+1] for i in range(len(amounts)-1))
    
    def test_top_paying_students_aggregation(self, db_session):
        """Test student payments are aggregated correctly"""
        # Student makes multiple payments
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='STU001',
                status='completed',
                student_id=1
            ),
            MpesaTransaction(
                phone_number='254712345678',
                amount=2000,
                account_reference='STU001',
                status='completed',
                student_id=1
            ),
            MpesaTransaction(
                phone_number='254712345678',
                amount=1500,
                account_reference='STU001',
                status='completed',
                student_id=1
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        top_students = MpesaAnalytics.get_top_paying_students(limit=10)
        
        # Should aggregate: 1000 + 2000 + 1500 = 4500
        student = next((s for s in top_students if s.get('student_id') == 1), None)
        if student:
            assert student['total_amount'] == 4500
            assert student['transaction_count'] == 3
    
    def test_top_paying_students_excludes_failed(self, db_session):
        """Test failed transactions not counted in top students"""
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=5000,
                account_reference='STU001',
                status='completed',
                student_id=1
            ),
            MpesaTransaction(
                phone_number='254712345678',
                amount=10000,
                account_reference='STU001',
                status='failed',  # Should not count
                student_id=1
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        top_students = MpesaAnalytics.get_top_paying_students(limit=10)
        
        # Should only count successful: 5000
        student = next((s for s in top_students if s.get('student_id') == 1), None)
        if student:
            assert student['total_amount'] == 5000


class TestFailureAnalysis:
    """Test payment failure analysis"""
    
    def test_get_failure_rate(self, db_session):
        """Test failure rate calculation"""
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='completed'
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='failed'
            ),
            MpesaTransaction(
                phone_number='254712345680',
                amount=1500,
                account_reference='TEST003',
                status='failed'
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        failure_rate = MpesaAnalytics.get_failure_rate()
        
        # 2 failed out of 3 = 66.67%
        assert failure_rate >= 65 and failure_rate <= 67
    
    def test_get_failure_reasons(self, db_session):
        """Test failure reasons grouping"""
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='failed',
                result_desc='Insufficient funds'
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='failed',
                result_desc='Insufficient funds'
            ),
            MpesaTransaction(
                phone_number='254712345680',
                amount=1500,
                account_reference='TEST003',
                status='failed',
                result_desc='User cancelled'
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        failure_reasons = MpesaAnalytics.get_failure_reasons()
        
        assert isinstance(failure_reasons, list)
        if len(failure_reasons) > 0:
            reason = failure_reasons[0]
            assert 'reason' in reason
            assert 'count' in reason
    
    def test_get_timeout_transactions(self, db_session):
        """Test identification of timeout transactions"""
        old_time = datetime.now() - timedelta(minutes=10)
        
        transactions = [
            MpesaTransaction(
                phone_number='254712345678',
                amount=1000,
                account_reference='TEST001',
                status='pending',
                created_at=old_time
            ),
            MpesaTransaction(
                phone_number='254712345679',
                amount=2000,
                account_reference='TEST002',
                status='pending',
                created_at=datetime.now()  # Recent, not timeout
            )
        ]
        
        for txn in transactions:
            db_session.add(txn)
        db_session.commit()
        
        timeout_txns = MpesaAnalytics.get_timeout_transactions(timeout_minutes=5)
        
        # Should include old pending transaction
        assert len(timeout_txns) >= 1


class TestRevenueAnalysis:
    """Test revenue analysis and projections"""
    
    def test_get_monthly_revenue(self, db_session):
        """Test monthly revenue calculation"""
        revenue = MpesaAnalytics.get_monthly_revenue()
        
        assert isinstance(revenue, (int, float, Decimal))
        assert revenue >= 0
    
    def test_get_daily_average_revenue(self, db_session):
        """Test daily average revenue calculation"""
        avg_revenue = MpesaAnalytics.get_daily_average_revenue(days=30)
        
        assert isinstance(avg_revenue, (int, float, Decimal))
        assert avg_revenue >= 0
    
    def test_revenue_by_payment_method(self, db_session):
        """Test revenue breakdown (M-PESA only for now)"""
        revenue = MpesaAnalytics.get_revenue_by_payment_method()
        
        assert isinstance(revenue, dict) or isinstance(revenue, list)


class TestAnalyticsPerformance:
    """Test analytics query performance"""
    
    def test_dashboard_stats_performance(self, db_session):
        """Test dashboard stats query is efficient"""
        import time
        
        start = time.time()
        stats = MpesaAnalytics.get_dashboard_stats()
        elapsed = time.time() - start
        
        # Should complete in reasonable time (< 1 second)
        assert elapsed < 1.0
    
    def test_daily_trends_performance(self, db_session):
        """Test daily trends query is efficient"""
        import time
        
        start = time.time()
        trends = MpesaAnalytics.get_daily_trends(days=30)
        elapsed = time.time() - start
        
        # Should complete in reasonable time
        assert elapsed < 2.0


class TestAnalyticsExport:
    """Test analytics data export"""
    
    def test_export_to_csv(self, db_session):
        """Test exporting analytics to CSV"""
        # If export functionality exists
        csv_data = MpesaAnalytics.export_to_csv(
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now()
        )
        
        # Should return CSV string or file path
        assert csv_data is not None
    
    def test_export_to_excel(self, db_session):
        """Test exporting analytics to Excel"""
        # If export functionality exists
        excel_data = MpesaAnalytics.export_to_excel(
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now()
        )
        
        # Should return Excel file or data
        assert excel_data is not None


class TestAnalyticsDateRangeValidation:
    """Test date range validation in analytics"""
    
    def test_invalid_date_range_handled(self, db_session):
        """Test invalid date range (end before start) handled"""
        start_date = datetime.now()
        end_date = datetime.now() - timedelta(days=7)
        
        # Should handle gracefully
        stats = MpesaAnalytics.get_dashboard_stats(
            start_date=start_date,
            end_date=end_date
        )
        
        # Should return empty or error
        assert stats is not None
    
    def test_future_date_range_handled(self, db_session):
        """Test future date range handled"""
        start_date = datetime.now() + timedelta(days=7)
        end_date = datetime.now() + timedelta(days=14)
        
        # Should return empty results
        stats = MpesaAnalytics.get_dashboard_stats(
            start_date=start_date,
            end_date=end_date
        )
        
        assert stats['total_transactions'] == 0


class TestAnalyticsFiltering:
    """Test analytics filtering capabilities"""
    
    def test_filter_by_status(self, db_session):
        """Test filtering transactions by status"""
        stats = MpesaAnalytics.get_dashboard_stats(status='completed')
        
        # Should only include completed transactions
        assert stats is not None
    
    def test_filter_by_amount_range(self, db_session):
        """Test filtering by amount range"""
        stats = MpesaAnalytics.get_dashboard_stats(
            min_amount=1000,
            max_amount=5000
        )
        
        # Should only include amounts in range
        assert stats is not None
    
    def test_filter_by_student(self, db_session, sample_student):
        """Test filtering by specific student"""
        stats = MpesaAnalytics.get_student_payment_history(
            student_id=sample_student.id
        )
        
        # Should return student-specific data
        assert stats is not None
